"""Persistencia de las fichas importadas desde catálogos externos."""

from datetime import UTC, datetime
from typing import Any

from iptv_db.models.external_catalog import ExternalCatalogEpisode, ExternalCatalogItem
from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


class ExternalCatalogRepository:
    """Lee y actualiza Cinemeta sin mezclarlo con filas de streams IPTV."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def list_page(
        self, content_type: str, catalog_id: str, skip: int, page_size: int
    ) -> list[ExternalCatalogItem]:
        """Lista una página externa conservando el orden original del catálogo."""
        stmt = (
            select(ExternalCatalogItem)
            .where(
                ExternalCatalogItem.content_type == content_type,
                ExternalCatalogItem.catalog_id == catalog_id,
                ExternalCatalogItem.catalog_position >= skip,
            )
            .order_by(ExternalCatalogItem.catalog_position, ExternalCatalogItem.imdb_id)
            .limit(page_size)
        )
        return list(self.session.execute(stmt).scalars().all())

    def search_page(
        self, content_type: str, catalog_id: str, query: str, skip: int, page_size: int
    ) -> list[ExternalCatalogItem]:
        """Search the scraper-imported catalog without calling Cinemeta at request time."""
        pattern = f"%{query.strip()}%"
        starts_with = f"{query.strip()}%"
        stmt = (
            select(ExternalCatalogItem)
            .where(
                ExternalCatalogItem.content_type == content_type,
                ExternalCatalogItem.catalog_id == catalog_id,
                (
                    ExternalCatalogItem.title_es.ilike(pattern)
                    | ExternalCatalogItem.title.ilike(pattern)
                    | ExternalCatalogItem.imdb_id.ilike(pattern)
                ),
            )
            .order_by(
                case((ExternalCatalogItem.title_es.ilike(starts_with), 0), else_=1),
                case((ExternalCatalogItem.title.ilike(starts_with), 0), else_=1),
                ExternalCatalogItem.catalog_position,
                ExternalCatalogItem.imdb_id,
            )
            .offset(skip)
            .limit(page_size)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_by_imdb(self, content_type: str, imdb_id: str) -> ExternalCatalogItem | None:
        """Busca una ficha externa para enriquecer un título IPTV por IMDb."""
        stmt = (
            select(ExternalCatalogItem)
            .where(
                ExternalCatalogItem.content_type == content_type,
                ExternalCatalogItem.imdb_id == imdb_id,
            )
            .order_by(
                case((ExternalCatalogItem.overview_es.is_not(None), 0), else_=1),
                case((ExternalCatalogItem.title_es.is_not(None), 0), else_=1),
                ExternalCatalogItem.last_seen_at.desc(),
            )
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def list_episodes(self, imdb_id: str) -> list[ExternalCatalogEpisode]:
        """Lee los episodios externos ya enriquecidos por el scraper."""
        stmt = (
            select(ExternalCatalogEpisode)
            .where(ExternalCatalogEpisode.imdb_id == imdb_id)
            .order_by(
                ExternalCatalogEpisode.season_number,
                ExternalCatalogEpisode.episode_number,
            )
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_metadata_by_imdb_ids(
        self, content_type: str, imdb_ids: list[str]
    ) -> dict[str, ExternalCatalogItem]:
        """Devuelve metadatos externos para enriquecer una página IPTV en una sola consulta."""
        if not imdb_ids:
            return {}
        stmt = (
            select(ExternalCatalogItem)
            .where(
                ExternalCatalogItem.content_type == content_type,
                ExternalCatalogItem.imdb_id.in_(imdb_ids),
            )
            .order_by(
                case((ExternalCatalogItem.overview_es.is_not(None), 0), else_=1),
                ExternalCatalogItem.last_seen_at.desc(),
            )
        )
        result: dict[str, ExternalCatalogItem] = {}
        for item in self.session.execute(stmt).scalars():
            result.setdefault(item.imdb_id, item)
        return result

    def upsert_page(
        self,
        content_type: str,
        catalog_id: str,
        skip: int,
        items: list[dict[str, Any]],
    ) -> None:
        """Guarda una página de Cinemeta y actualiza las fichas ya conocidas."""
        now = datetime.now(UTC)
        rows = []
        for offset, item in enumerate(items):
            imdb_id = item.get("imdb_id") or item.get("id")
            if not imdb_id:
                continue
            rows.append(
                {
                    "content_type": content_type,
                    "catalog_id": catalog_id,
                    "catalog_position": skip + offset,
                    "imdb_id": imdb_id,
                    "moviedb_id": item.get("moviedb_id"),
                    "title": item.get("title"),
                    "description_en": item.get("description"),
                    "poster": item.get("poster"),
                    "backdrop": item.get("backdrop"),
                    "rating": item.get("rating"),
                    "year": item.get("year"),
                    "imported_at": now,
                    "last_seen_at": now,
                    "updated_at": now,
                }
            )
        if not rows:
            return

        stmt = insert(ExternalCatalogItem).values(rows)
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            constraint="uq_external_catalog_items_identity",
            set_={
                "catalog_position": excluded.catalog_position,
                "moviedb_id": excluded.moviedb_id,
                "title": excluded.title,
                "description_en": excluded.description_en,
                "poster": excluded.poster,
                "backdrop": excluded.backdrop,
                "rating": excluded.rating,
                "year": excluded.year,
                "last_seen_at": excluded.last_seen_at,
                "updated_at": excluded.updated_at,
            },
        )
        self.session.execute(stmt)
