"""Shim: re-exports models from iptv-db. Source of truth is iptv_db.models."""

from iptv_db.models.config import Config, SyncMetadata

__all__ = ["Config", "SyncMetadata"]
