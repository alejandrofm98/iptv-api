"""Shim: re-exports models from iptv-db. Source of truth is iptv_db.models."""

from iptv_db.models.scraper import ScraperFailure

__all__ = ["ScraperFailure"]
