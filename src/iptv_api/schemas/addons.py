from pydantic import BaseModel, Field


class AddonEpisode(BaseModel):
    id: str
    season: int | None = None
    episode: int | None = None
    title: str | None = None
    overview: str | None = None
    thumbnail: str | None = None
    released: str | None = None


class AddonMetaResponse(BaseModel):
    imdb_id: str
    content_type: str = Field(description="movie o series")
    name: str | None = None
    year: str | None = None
    description_en: str | None = None
    overview_es: str | None = None
    title_es: str | None = None
    overview_source: str = Field(description="tmdb cuando hay sinopsis ES, none en caso contrario")
    poster: str | None = None
    background: str | None = None
    logo: str | None = None
    genres: list[str] = Field(default_factory=list)
    cast: list[str] = Field(default_factory=list)
    imdb_rating: str | None = None
    moviedb_id: int | None = None
    has_torrent_source: bool = False
    torrent_languages: list[str] = Field(default_factory=list)
    torrent_status: str = Field(description="ok, unavailable o not_requested")
    total_episodes: int = 0
    seasons: list[int] = Field(default_factory=list)
    episodes: list[AddonEpisode] = Field(default_factory=list)


class AddonCatalogItem(BaseModel):
    id: str
    title: str | None = None
    type: str
    description: str | None = None
    poster: str | None = None
    backdrop: str | None = None
    logo: str | None = None
    genres: list[str] = Field(default_factory=list)
    rating: float | None = None
    year: int | None = None
    imdb_id: str | None = None
    moviedb_id: int | None = None


class AddonCatalogResponse(BaseModel):
    items: list[AddonCatalogItem] = Field(default_factory=list)
    content_type: str
    catalog_id: str
    skip: int = 0
    has_next: bool = False
