"""Compone respuestas del catálogo Cinemeta ya persistido en PostgreSQL."""

from sqlalchemy.orm import Session

from iptv_api.repositories.external_catalog_repo import ExternalCatalogRepository


class AddonCatalogService:
    """Mantiene las peticiones de catálogo libres de llamadas externas y escrituras."""

    def __init__(self, session: Session) -> None:
        self.repository = ExternalCatalogRepository(session)

    def catalog(
        self, content_type: str, catalog_id: str, skip: int, page_size: int, search: str | None
    ) -> dict:
        cached = (
            self.repository.search_page(content_type, catalog_id, search, skip, page_size)
            if search
            else self.repository.list_page(content_type, catalog_id, skip, page_size)
        )
        localized = self.repository.get_metadata_by_imdb_ids(
            content_type, [row.imdb_id for row in cached]
        )
        items = []
        for row in cached:
            detail = localized.get(row.imdb_id)
            items.append(
                {
                    "id": row.imdb_id,
                    "imdb_id": row.imdb_id,
                    "moviedb_id": row.moviedb_id,
                    "title": row.title_es or (detail.title_es if detail else None) or row.title,
                    "type": row.content_type,
                    "description": row.overview_es or (detail.overview_es if detail else None),
                    "overview_en": row.description_en
                    or (detail.description_en if detail else None),
                    "poster": row.poster,
                    "backdrop": row.backdrop,
                    "logo": row.logo or (detail.logo if detail else None),
                    "genres": row.genres or (detail.genres if detail else None) or [],
                    "rating": row.rating,
                    "year": row.year,
                }
            )
        has_next = bool(
            not search
            and len(cached) == page_size
            and self.repository.list_page(content_type, catalog_id, skip + len(cached), 1)
        )
        return {
            "items": items,
            "content_type": content_type,
            "catalog_id": catalog_id,
            "skip": skip,
            "has_next": has_next,
        }

    def meta(self, content_type: str, imdb_id: str, include_videos: bool) -> dict | None:
        row = self.repository.get_by_imdb(content_type, imdb_id)
        if row is None:
            return None
        saved_episodes = self.repository.list_episodes(imdb_id) if content_type == "series" else []
        episodes = [
            {
                "id": episode.video_id,
                "season": episode.season_number,
                "episode": episode.episode_number,
                "title": episode.title_es or episode.title_en,
                "overview": episode.overview_es or episode.overview_en,
                "overview_es": episode.overview_es,
                "overview_en": episode.overview_en,
                "thumbnail": episode.thumbnail,
                "released": episode.released,
            }
            for episode in saved_episodes
        ]
        return {
            "imdb_id": imdb_id,
            "content_type": content_type,
            "name": row.title_es or row.title,
            "year": str(row.year) if row.year else None,
            "description_en": row.description_en,
            "overview_es": row.overview_es,
            "title_es": row.title_es,
            "overview_source": "tmdb" if row.overview_es else "none",
            "poster": row.poster,
            "background": row.backdrop,
            "logo": row.logo,
            "genres": row.genres or [],
            "cast": row.cast or [],
            "imdb_rating": str(row.rating) if row.rating is not None else None,
            "moviedb_id": row.moviedb_id,
            "total_episodes": len(saved_episodes),
            "seasons": sorted({episode.season_number for episode in saved_episodes}),
            "episodes": episodes if include_videos else [],
        }
