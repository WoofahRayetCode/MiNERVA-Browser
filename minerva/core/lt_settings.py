"""libtorrent session configuration.

Kept separate from ``torrent_engine`` so the settings can be validated against the
installed libtorrent without constructing a session: libtorrent 2.x removed several
1.x settings, and ``lt.session(dict)`` / ``apply_settings`` raise ``KeyError`` for the
whole pack if even one key is unknown.  :func:`build_session_settings` drops unknown keys
and reports them instead of silently running on libtorrent's defaults.
"""
from __future__ import annotations

from typing import Iterable

# Superset of libtorrent's own default bootstrap list (which only has dht.libtorrent.org).
DHT_BOOTSTRAP_NODES = (
    "router.bittorrent.com:6881,"
    "router.utorrent.com:6881,"
    "dht.transmissionbt.com:6881,"
    "dht.libtorrent.org:25401,"
    "router.bitcomet.com:6881,"
    "dht.aelitis.com:6881"
)

# Alert categories the engine consumes.  ``file_completed_alert`` is posted under
# ``file_progress_notification`` (not ``status``).  ``tracker_notification`` is left out on
# purpose: with ~50 trackers per torrent it is far too chatty, and tracker *errors* already
# arrive through ``error_notification``.
_ALERT_CATEGORIES = (
    "error_notification",
    "status_notification",
    "storage_notification",
    "file_progress_notification",
)


def compute_alert_mask(lt_module) -> int:
    """Bitmask of the alert categories the engine needs (unknown names are ignored)."""
    categories = lt_module.alert.category_t
    mask = 0
    for name in _ALERT_CATEGORIES:
        value = getattr(categories, name, None)
        if value is not None:
            mask |= int(value)
    return mask


def optimized_session_settings(alert_mask: int | None = None) -> dict:
    """Tuned libtorrent settings for throughput and fast peer discovery.

    Every key must exist in libtorrent 2.x; ``tests/test_lt_settings.py`` enforces that
    against the installed build.  ``inactivity_timeout`` / ``peer_timeout`` are
    deliberately left at libtorrent's defaults: short values cause peer churn on
    low-seed archive torrents.
    """
    settings = {
        # Concurrency & connections
        "connections_limit": 500,
        "connection_speed": 100,
        "torrent_connect_boost": 50,
        "unchoke_slots_limit": 80,
        "num_optimistic_unchoke_slots": 8,
        "max_peerlist_size": 4000,
        "max_paused_peerlist_size": 1000,
        "max_pex_peers": 100,

        # Pipelining & request queues
        "request_queue_time": 3,
        "max_out_request_queue": 1500,
        "max_allowed_in_request_queue": 2000,
        "whole_pieces_threshold": 20,
        "piece_timeout": 20,
        "request_timeout": 30,
        "peer_connect_timeout": 15,
        "min_reconnect_time": 2,

        # Socket & network buffers
        "recv_socket_buffer_size": 2 * 1024 * 1024,
        "send_socket_buffer_size": 2 * 1024 * 1024,
        "max_peer_recv_buffer_size": 4 * 1024 * 1024,

        # Protocols & discovery
        "enable_dht": True,
        "enable_lsd": True,
        "enable_upnp": True,
        "enable_natpmp": True,
        "enable_outgoing_tcp": True,
        "enable_incoming_tcp": True,
        "enable_outgoing_utp": True,
        "enable_incoming_utp": True,
        "use_dht_as_fallback": False,
        "dht_aggressive_lookups": True,
        "dht_bootstrap_nodes": DHT_BOOTSTRAP_NODES,

        # DownloadQueue owns concurrency, so libtorrent must not queue torrents itself.
        "active_downloads": -1,
        "active_seeds": -1,
        "active_limit": -1,
        "active_tracker_limit": -1,
        "active_dht_limit": -1,
        "active_lsd_limit": -1,

        # Trackers: we inject ~50 public trackers (in a tier after the torrent's own), so
        # announce to every tracker in every tier, not just the first one that answers.
        "announce_to_all_trackers": True,
        "announce_to_all_tiers": True,
        "tracker_completion_timeout": 30,
        "tracker_receive_timeout": 15,
        "stop_tracker_timeout": 1,

        # Misc
        "close_redundant_connections": True,
        "allow_multiple_connections_per_ip": True,
        "alert_queue_size": 10000,
    }
    if alert_mask is not None:
        settings["alert_mask"] = int(alert_mask)
    return settings


def build_session_settings(
    known_keys: Iterable[str] | None,
    settings: dict,
    overrides: dict | None = None,
) -> tuple[dict, list[str]]:
    """Return ``(usable_settings, skipped_keys)``.

    ``known_keys`` is the set of setting names the installed libtorrent understands
    (``lt.default_settings().keys()``); ``None`` disables filtering.  ``overrides`` are
    applied last (used by tests to keep sessions off the network).
    """
    merged = dict(settings)
    if overrides:
        merged.update(overrides)
    if known_keys is None:
        return merged, []
    known = set(known_keys)
    usable = {k: v for k, v in merged.items() if k in known}
    skipped = sorted(k for k in merged if k not in known)
    return usable, skipped
