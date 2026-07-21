"""Shim: re-exports models from iptv-db. Source of truth is iptv_db.models."""

from iptv_db.models.content import MovieCatalog, MovieMetadata, MovieStream

__all__ = ["MovieCatalog", "MovieMetadata", "MovieStream"]
