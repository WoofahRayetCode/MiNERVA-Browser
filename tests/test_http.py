import email.utils
import http.server
import pathlib
import tempfile
import threading
import time
import unittest

from minerva.core import http as mhttp


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        n = self.hits[path] = self.hits.get(path, 0) + 1
        if path == "/ok":
            self._send(200, b"hello")
        elif path == "/flaky":  # 503 twice, then fine
            self._send(503) if n <= 2 else self._send(200, b"finally")
        elif path == "/limited":  # 429 + Retry-After once
            self._send(429, headers={"Retry-After": "7"}) if n == 1 else self._send(200, b"after-wait")
        elif path == "/always503":
            self._send(503)
        elif path == "/gone":
            self._send(404, b"nope")
        elif path == "/forbidden":
            self._send(403)
        elif path == "/truncated":  # promises 100 bytes, delivers 5, then hangs up
            if n == 1:
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"short")
                self.wfile.flush()
                self.close_connection = True
            else:
                self._send(200, b"x" * 100)
        elif path == "/big":
            self._send(200, b"y" * 5000)
        else:
            self._send(404)


class TestGetBytes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.hits.clear()
        self.delays: list[float] = []

    def get(self, path, **kw):
        kw.setdefault("sleep", self.delays.append)
        kw.setdefault("rng", lambda: 1.0)  # deterministic full jitter
        return mhttp.get_bytes(self.base + path, timeout=5, **kw)

    def test_success(self):
        self.assertEqual(self.get("/ok"), b"hello")
        self.assertEqual(self.delays, [])

    def test_retries_5xx_with_exponential_backoff_then_succeeds(self):
        self.assertEqual(self.get("/flaky"), b"finally")
        self.assertEqual(_Handler.hits["/flaky"], 3)
        self.assertEqual(self.delays, [1.0, 2.0])

    def test_gives_up_after_retries_and_reports_retryable(self):
        with self.assertRaises(mhttp.HttpError) as ctx:
            self.get("/always503", retries=2)
        self.assertEqual(ctx.exception.status, 503)
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(_Handler.hits["/always503"], 3)

    def test_429_honours_retry_after_and_penalizes_the_shared_gate(self):
        gate = mhttp.RateGate(rate_per_sec=1000)
        penalties = []
        gate.penalize = lambda s: penalties.append(s)
        self.assertEqual(self.get("/limited", gate=gate), b"after-wait")
        self.assertEqual(penalties, [7.0])
        self.assertGreaterEqual(self.delays[0], 7.0)

    def test_does_not_retry_permanent_errors(self):
        for path, status in (("/gone", 404), ("/forbidden", 403)):
            with self.assertRaises(mhttp.HttpError) as ctx:
                self.get(path)
            self.assertEqual(ctx.exception.status, status)
            self.assertFalse(ctx.exception.retryable)
            self.assertEqual(_Handler.hits[path], 1)
        self.assertEqual(self.delays, [])

    def test_truncated_body_is_detected_and_retried(self):
        self.assertEqual(self.get("/truncated"), b"x" * 100)
        self.assertEqual(_Handler.hits["/truncated"], 2)

    def test_connection_refused_is_retried_then_raises(self):
        dead = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        port = dead.server_address[1]
        dead.server_close()
        with self.assertRaises(mhttp.HttpError) as ctx:
            mhttp.get_bytes(f"http://127.0.0.1:{port}/x", timeout=2, retries=2,
                            sleep=self.delays.append, rng=lambda: 1.0)
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(self.delays, [1.0, 2.0])

    def test_max_bytes_rejects_oversized_responses_without_retrying(self):
        with self.assertRaises(mhttp.HttpError) as ctx:
            self.get("/big", max_bytes=1000)
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(_Handler.hits["/big"], 1)
        self.assertEqual(len(self.get("/big", max_bytes=10_000)), 5000)

    def test_cancel_stops_before_any_request(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(mhttp.HttpError):
            self.get("/ok", cancel=cancel)
        self.assertNotIn("/ok", _Handler.hits)


class TestHelpers(unittest.TestCase):
    def test_parse_retry_after(self):
        self.assertEqual(mhttp.parse_retry_after("5"), 5.0)
        self.assertEqual(mhttp.parse_retry_after("99999"), mhttp.MAX_RETRY_AFTER)
        self.assertIsNone(mhttp.parse_retry_after(""))
        self.assertIsNone(mhttp.parse_retry_after(None))
        self.assertIsNone(mhttp.parse_retry_after("soon"))
        now = 1_700_000_000.0
        later = email.utils.formatdate(now + 30, usegmt=True)
        self.assertAlmostEqual(mhttp.parse_retry_after(later, now=now), 30.0, delta=1.0)
        past = email.utils.formatdate(now - 30, usegmt=True)
        self.assertEqual(mhttp.parse_retry_after(past, now=now), 0.0)

    def test_backoff_is_bounded_full_jitter(self):
        self.assertEqual(mhttp.backoff_delay(0, rng=lambda: 1.0), 1.0)
        self.assertEqual(mhttp.backoff_delay(3, rng=lambda: 1.0), 8.0)
        self.assertEqual(mhttp.backoff_delay(10, rng=lambda: 1.0), 30.0)  # capped
        self.assertEqual(mhttp.backoff_delay(3, rng=lambda: 0.0), 0.0)

    def test_rate_gate_spaces_requests_and_applies_penalty(self):
        now = [100.0]
        slept: list[float] = []

        def sleep(s):
            slept.append(round(s, 3))
            now[0] += s

        gate = mhttp.RateGate(rate_per_sec=4, clock=lambda: now[0], sleep=sleep)
        gate.wait()  # first caller passes immediately
        gate.wait()
        gate.wait()
        self.assertEqual(slept, [0.25, 0.25])  # one slot every 0.25 s (4 req/s)
        self.assertAlmostEqual(now[0], 100.5)
        gate.penalize(10)
        slept.clear()
        gate.wait()
        self.assertGreaterEqual(slept[0], 9.0)

    def test_atomic_write_validates_before_touching_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "x.torrent"
            mhttp.atomic_write_bytes(dest, b"good")

            def reject(data):
                raise ValueError("not a torrent")

            with self.assertRaises(ValueError):
                mhttp.atomic_write_bytes(dest, b"<html>error page</html>", reject)
            self.assertEqual(dest.read_bytes(), b"good")
            self.assertEqual([p.name for p in pathlib.Path(tmp).iterdir()], ["x.torrent"])

    def test_atomic_write_cleans_up_partial_file_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp) / "nodir" / "x.torrent"  # parent missing -> open() fails
            with self.assertRaises(OSError):
                mhttp.atomic_write_bytes(dest, b"data")
            self.assertEqual(list(pathlib.Path(tmp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
