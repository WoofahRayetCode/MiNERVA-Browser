"""Helpers for building tiny synthetic torrents (no payload data, fake piece hashes)."""
import hashlib

PIECE = 16384


def make_torrent_bytes(names, *, piece_length=PIECE, file_size=None, collection="coll",
                       announce=b"udp://tracker.invalid:1/announce") -> bytes:
    """Bencoded multi-file torrent whose files are ``names`` (``a/b.bin`` makes a subfolder)."""
    import libtorrent as lt

    size = file_size or piece_length * 2
    total = size * len(names)
    pieces = b"".join(hashlib.sha1(b"x").digest() for _ in range(-(-total // piece_length)))
    info = {
        b"name": collection.encode(),
        b"piece length": piece_length,
        b"pieces": pieces,
        b"files": [
            {b"length": size, b"path": [part.encode() for part in name.split("/")]}
            for name in names
        ],
    }
    return lt.bencode({b"announce": announce, b"info": info})


# ---- a real localhost seeder ------------------------------------------------------------------
import pathlib  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402

OFFLINE_SETTINGS = {
    "listen_interfaces": "127.0.0.1:0",
    "enable_dht": False,
    "enable_lsd": False,
    "enable_upnp": False,
    "enable_natpmp": False,
}


def payload(name: str, size: int) -> bytes:
    """Deterministic pseudo-random bytes for ``name`` (so tests can verify content)."""
    out = bytearray()
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f"{name}:{counter}".encode()).digest()
        counter += 1
    return bytes(out[:size])


class Seeder:
    """Seeds a synthetic multi-file collection with real piece hashes on 127.0.0.1."""

    def __init__(self, names, *, file_size=PIECE * 3, collection="coll", settings=None):
        import libtorrent as lt

        self.lt = lt
        self.names = list(names)
        self.collection = collection
        self.files = {n: payload(n, file_size) for n in self.names}
        self.root = pathlib.Path(tempfile.mkdtemp(prefix="seeder-"))
        for name, data in self.files.items():
            path = self.root / collection / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        blob = b"".join(self.files[n] for n in self.names)
        pieces = b"".join(hashlib.sha1(blob[i:i + PIECE]).digest() for i in range(0, len(blob), PIECE))
        info = {
            b"name": collection.encode(),
            b"piece length": PIECE,
            b"pieces": pieces,
            b"files": [
                {b"length": len(self.files[n]), b"path": [p.encode() for p in n.split("/")]}
                for n in self.names
            ],
        }
        self.torrent_bytes = lt.bencode({b"info": info})
        self.torrent_path = self.root / "collection.torrent"
        self.torrent_path.write_bytes(self.torrent_bytes)
        ti = lt.torrent_info(lt.bdecode(self.torrent_bytes))
        self.info_hash = str(ti.info_hashes().get_best())
        self.session = lt.session({**OFFLINE_SETTINGS, **(settings or {})})
        params = lt.add_torrent_params()
        params.ti = ti
        params.save_path = str(self.root)
        params.flags &= ~(lt.torrent_flags.paused | lt.torrent_flags.auto_managed)
        self.handle = self.session.add_torrent(params)

    def connect_to(self, port: int):
        """Dial the leecher; harmless to repeat (the leecher may not have added the torrent yet)."""
        self.handle.connect_peer(("127.0.0.1", port))

    def close(self):
        try:
            self.session.abort()
        except Exception:
            pass


def wait_until(predicate, timeout=20.0, interval=0.05, tick=None):
    end = time.time() + timeout
    while time.time() < end:
        if tick is not None:
            tick()
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()
