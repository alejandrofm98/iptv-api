from datetime import datetime

from pydantic import BaseModel, Field


class StreamOption(BaseModel):
    url: str = ""
    label: str = "Ver"
    country: str | None = None
    quality: str | None = None
    provider_id: str | None = None
    numero: int | None = None
    source: str | None = None
    provider: str | None = None
    language: str | None = None
    playable: bool = True
    requires_resolution: bool = False
    info_hash: str | None = None
    file_idx: int | None = None
    seeders: int | None = None
    size_bytes: int | None = None
    title: str | None = None


class SkipSegment(BaseModel):
    start_ms: int
    end_ms: int
    confidence: float | None = None
    submission_count: int | None = None


class SkipSegments(BaseModel):
    intro: SkipSegment | None = None
    recap: SkipSegment | None = None
    outro: SkipSegment | None = None


class MovieCatalogItem(BaseModel):
    id: str
    title: str
    provider_id: str | None = None
    tmdb_id: str | None = None
    year: int | None = None
    group_normalizado: str | None = None
    logo: str | None = None
    poster_path: str | None = Field(None, alias="tmdb_poster_path")
    backdrop_path: str | None = None
    vote_average: float | None = None
    created_at: datetime | None = None
    has_iptv_source: bool = False
    has_torrent_source: bool = False
    torrent_source_checked_at: datetime | None = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class MovieWithMetadata(MovieCatalogItem):
    overview: str | None = None
    overview_es: str | None = None
    overview_en: str | None = None
    vote_count: int | None = None
    genres: list[str] | None = None
    tmdb_title: str | None = None
    release_date: str | None = None
    runtime_minutes: int | None = None
    popularity: float | None = None
    status: str | None = None
    tagline: str | None = None
    stream_options: list[StreamOption] | None = None


class SeriesCatalogItem(BaseModel):
    id: str
    title: str
    series_key: str | None = None
    provider_id: str | None = None
    tmdb_id: str | None = None
    year: int | None = None
    group_normalizado: str | None = None
    logo: str | None = None
    poster_path: str | None = Field(None, alias="tmdb_poster_path")
    backdrop_path: str | None = None
    vote_average: float | None = None
    total_episodes: int | None = None
    total_seasons: int | None = None
    created_at: datetime | None = None
    has_iptv_source: bool = False
    has_torrent_source: bool = False
    torrent_source_checked_at: datetime | None = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class SeriesWithMetadata(SeriesCatalogItem):
    overview: str | None = None
    overview_es: str | None = None
    overview_en: str | None = None
    vote_count: int | None = None
    genres: list[str] | None = None
    tmdb_title: str | None = None
    release_date: str | None = None
    popularity: float | None = None
    status: str | None = None
    tagline: str | None = None
    metadata_tmdb_id: str | None = None


class SeriesEpisodeItem(BaseModel):
    id: str
    catalog_id: str | None = None
    season_number: int
    episode_number: int
    title: str | None = None
    overview: str | None = None
    still_path: str | None = None
    stream_url: str | None = None
    url: str | None = None
    country: str | None = None
    quality: str | None = None
    provider_id: str | None = None
    numero: int | None = None
    imdb_id: str | None = None
    skip_segments: SkipSegments | None = None
