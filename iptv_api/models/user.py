"""Shim: re-exports models from iptv-db. Source of truth is iptv_db.models."""

from iptv_db.models.user import ActiveSession, User

__all__ = ["ActiveSession", "User"]
