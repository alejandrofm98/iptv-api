from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class WatchProgressUpsert(BaseModel):
    content_type: str
    position_ms: int
    duration_ms: int
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    title: Optional[str] = ""
    image_url: Optional[str] = ""


class WatchProgressResponse(BaseModel):
    id: str
    user_id: str
    content_id: str
    content_type: str
    position_ms: int
    duration_ms: int
    series_name: Optional[str] = None
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    title: str
    image_url: str
    last_watched_at: datetime
    is_watched: bool
    progress_percent: int = 0

    model_config = {"from_attributes": True}


class ContinueWatchingItem(WatchProgressResponse):
    normalized_title: Optional[str] = None
    overview: Optional[str] = None
    overview_es: Optional[str] = None
    overview_en: Optional[str] = None
    rating: Optional[float] = None
    vote_average: Optional[float] = None
    vote_count: Optional[int] = None
    genres: Optional[List[str]] = None
    poster_path: Optional[str] = None
    backdrop_path: Optional[str] = None
    runtime_minutes: Optional[int] = None
    tagline: Optional[str] = None
    release_date: Optional[str] = None
    year: Optional[int] = None
    tmdb_id: Optional[str] = None
    tmdb_title: Optional[str] = None
    popularity: Optional[float] = None
    status: Optional[str] = None
    total_seasons: Optional[int] = None
