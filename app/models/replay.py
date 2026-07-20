"""Shim: re-exports models from iptv-db. Source of truth is iptv_db.models."""

from iptv_db.models.replay import Replay

__all__ = ["Replay"]
