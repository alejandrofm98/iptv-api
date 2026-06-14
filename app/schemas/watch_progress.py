from datetime import datetime

from pydantic import BaseModel


class WatchProgressUpsert(BaseModel):
    content_type: str
    position_ms: int
    duration_ms: int
    series_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    title: str | None = ""
    image_url: str | None = ""


class WatchProgressResponse(BaseModel):
    id: str
    user_id: str
    content_id: str
    content_type: str
    position_ms: int
    duration_ms: int
    series_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    title: str
    image_url: str
    last_watched_at: datetime
    is_watched: bool
    progress_percent: int = 0

    model_config = {"from_attributes": True}


class ContinueWatchingItem(WatchProgressResponse):
    series_provider_id: str | None = None
    normalized_title: str | None = None
    overview: str | None = None
    overview_es: str | None = None
    overview_en: str | None = None
    rating: float | None = None
    vote_average: float | None = None
    vote_count: int | None = None
    genres: list[str] | None = None
    poster_path: str | None = None
    backdrop_path: str | None = None
    runtime_minutes: int | None = None
    tagline: str | None = None
    release_date: str | None = None
    year: int | None = None
    tmdb_id: str | None = None
    tmdb_title: str | None = None
    popularity: float | None = None
    status: str | None = None
    total_seasons: int | None = None
