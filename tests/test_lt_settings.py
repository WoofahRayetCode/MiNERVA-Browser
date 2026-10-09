import unittest

from minerva.core import lt_settings
from minerva.core.torrent_engine import _get_optimized_session_settings, _LT_AVAILABLE, lt

# Keep test sessions off the network.
_OFFLINE = {
    "listen_interfaces": "127.0.0.1:0",
    "enable_dht": False,
    "enable_lsd": False,
    "enable_upnp": False,
    "enable_natpmp": False,
}


class TestBuildSessionSettings(unittest.TestCase):
    def test_unknown_keys_are_dropped_and_reported(self):
        usable, skipped = lt_settings.build_session_settings(
            ["a", "b"], {"a": 1, "b": 2, "gone": 3, "also_gone": 4}
        )
        self.assertEqual(usable, {"a": 1, "b": 2})
        self.assertEqual(skipped, ["also_gone", "gone"])

    def test_none_known_keys_disables_filtering(self):
        usable, skipped = lt_settings.build_session_settings(None, {"a": 1})
        self.assertEqual(usable, {"a": 1})
        self.assertEqual(skipped, [])

    def test_overrides_win_and_are_filtered_too(self):
        usable, skipped = lt_settings.build_session_settings(
            ["a", "b"], {"a": 1, "b": 2}, {"a": 10, "nope": 1}
        )
        self.assertEqual(usable, {"a": 10, "b": 2})
        self.assertEqual(skipped, ["nope"])

    def test_inputs_are_not_mutated(self):
        base = {"a": 1}
        lt_settings.build_session_settings(["a"], base, {"a": 2})
        self.assertEqual(base, {"a": 1})

    def test_does_not_override_libtorrent_peer_timeouts(self):
        # Short inactivity/peer timeouts cause churn on low-seed archive torrents.
        settings = lt_settings.optimized_session_settings()
        self.assertNotIn("inactivity_timeout", settings)
        self.assertNotIn("peer_timeout", settings)


@unittest.skipUnless(_LT_AVAILABLE, "libtorrent not installed")
class TestAgainstInstalledLibtorrent(unittest.TestCase):
    def test_every_setting_key_exists_in_installed_libtorrent(self):
        # libtorrent raises KeyError for the *whole pack* on one unknown key, which
        # previously left the session silently on defaults.
        known = set(lt.default_settings())
        unknown = sorted(k for k in _get_optimized_session_settings() if k not in known)
        self.assertEqual(unknown, [], f"settings not supported by libtorrent {lt.__version__}")

    def test_alert_mask_includes_file_progress(self):
        mask = lt_settings.compute_alert_mask(lt)
        categories = lt.alert.category_t
        for name in ("error_notification", "status_notification",
                     "storage_notification", "file_progress_notification"):
            self.assertTrue(mask & int(getattr(categories, name)), name)
        self.assertFalse(mask & int(categories.tracker_notification))

    def test_engine_session_really_uses_tuned_settings(self):
        from minerva.core.torrent_engine import TorrentEngine

        engine = TorrentEngine(settings_overrides=_OFFLINE)
        try:
            applied = engine._session.get_settings()
            self.assertGreaterEqual(applied["connections_limit"], 500)
            self.assertEqual(applied["active_downloads"], -1)
            self.assertEqual(applied["active_limit"], -1)
            self.assertTrue(applied["announce_to_all_trackers"])
            self.assertTrue(applied["announce_to_all_tiers"])
            self.assertEqual(applied["alert_mask"], lt_settings.compute_alert_mask(lt))
        finally:
            engine.shutdown()

    def test_added_torrents_are_neither_paused_nor_auto_managed(self):
        from minerva.core.torrent_engine import _prepare_add_params

        params = _prepare_add_params(lt.add_torrent_params())
        flags = lt.torrent_flags
        self.assertFalse(params.flags & flags.paused)
        self.assertFalse(params.flags & flags.auto_managed)

        magnet = _prepare_add_params(lt.add_torrent_params(), defer_download=True)
        self.assertTrue(magnet.flags & flags.default_dont_download)


if __name__ == "__main__":
    unittest.main()
