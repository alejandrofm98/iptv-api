from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class StreamOption(BaseModel):
    url: str
    label: str
    country: Optional[str] = None
    provider_id: Optional[str] = None
    numero: Optional[int] = None


class MovieCatalogItem(BaseModel):
    id: str
    title: str
    provider_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    year: Optional[int] = None
    country: Optional[str] = None
    group_normalizado: Optional[str] = None
    logo: Optional[str] = None
    poster_path: Optional[str] = Field(None, alias="tmdb_poster_path")
    backdrop_path: Optional[str] = None
    vote_average: Optional[float] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class MovieWithMetadata(MovieCatalogItem):
    overview: Optional[str] = None
    overview_es: Optional[str] = None
    overview_en: Optional[str] = None
    vote_count: Optional[int] = None
    genres: Optional[List[str]] = None
    tmdb_title: Optional[str] = None
    release_date: Optional[str] = None
    runtime_minutes: Optional[int] = None
    popularity: Optional[float] = None
    status: Optional[str] = None
    tagline: Optional[str] = None
    stream_options: Optional[List[StreamOption]] = None


class SeriesCatalogItem(BaseModel):
    id: str
    title: str
    series_key: Optional[str] = None
    provider_id: Optional[str] = None
    tmdb_id: Optional[str] = None
    year: Optional[int] = None
    country: Optional[str] = None
    group_normalizado: Optional[str] = None
    logo: Optional[str] = None
    poster_path: Optional[str] = Field(None, alias="tmdb_poster_path")
    backdrop_path: Optional[str] = None
    vote_average: Optional[float] = None
    total_episodes: Optional[int] = None
    total_seasons: Optional[int] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class SeriesWithMetadata(SeriesCatalogItem):
    overview: Optional[str] = None
    overview_es: Optional[str] = None
    overview_en: Optional[str] = None
    vote_count: Optional[int] = None
    genres: Optional[List[str]] = None
    tmdb_title: Optional[str] = None
    release_date: Optional[str] = None
    popularity: Optional[float] = None
    status: Optional[str] = None
    tagline: Optional[str] = None
    metadata_tmdb_id: Optional[str] = None


class SeriesEpisodeItem(BaseModel):
    id: str
    catalog_id: Optional[str] = None
    season_number: int
    episode_number: int
    title: Optional[str] = None
    overview: Optional[str] = None
    still_path: Optional[str] = None
    stream_url: Optional[str] = None
    url: Optional[str] = None
    country: Optional[str] = None
    quality: Optional[str] = None
    provider_id: Optional[str] = None
    numero: Optional[int] = None
