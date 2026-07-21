"""Model shims: re-exports all ORM models from iptv-db (source of truth)."""

from iptv_db.models import (
    ActiveSession,
    Channel,
    ChannelFavorite,
    Config,
    MovieCatalog,
    MovieMetadata,
    MovieStream,
    Replay,
    ScraperFailure,
    SeriesCatalog,
    SeriesEpisode,
    SeriesMetadata,
    SeriesStream,
    SyncMetadata,
    User,
    WatchProgress,
)

__all__ = [
    "ActiveSession",
    "Channel",
    "ChannelFavorite",
    "Config",
    "MovieCatalog",
    "MovieMetadata",
    "MovieStream",
    "Replay",
    "ScraperFailure",
    "SeriesCatalog",
    "SeriesEpisode",
    "SeriesMetadata",
    "SeriesStream",
    "SyncMetadata",
    "User",
    "WatchProgress",
]
