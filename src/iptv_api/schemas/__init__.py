from iptv_api.schemas.channel import ChannelFavoriteResponse, ChannelItem
from iptv_api.schemas.common import PaginatedResponse, PaginationParams
from iptv_api.schemas.content import (
    MovieCatalogItem,
    MovieWithMetadata,
    SeriesCatalogItem,
    SeriesEpisodeItem,
    SeriesWithMetadata,
)
from iptv_api.schemas.playback_preference import (
    PlaybackPreferenceResponse,
    PlaybackPreferenceUpdate,
)
from iptv_api.schemas.user import UserCreate, UserResponse, UserUpdate
from iptv_api.schemas.watch_progress import (
    ContinueWatchingItem,
    WatchProgressResponse,
    WatchProgressUpsert,
)

__all__ = [
    "ChannelFavoriteResponse",
    "ChannelItem",
    "ContinueWatchingItem",
    "MovieCatalogItem",
    "MovieWithMetadata",
    "PaginatedResponse",
    "PaginationParams",
    "PlaybackPreferenceResponse",
    "PlaybackPreferenceUpdate",
    "SeriesCatalogItem",
    "SeriesEpisodeItem",
    "SeriesWithMetadata",
    "UserCreate",
    "UserResponse",
    "UserUpdate",
    "WatchProgressResponse",
    "WatchProgressUpsert",
]
