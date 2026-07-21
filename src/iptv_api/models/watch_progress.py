"""Shim: re-exports models from iptv-db. Source of truth is iptv_db.models."""

from iptv_db.models.watch_progress import WatchProgress

__all__ = ["WatchProgress"]
