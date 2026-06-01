from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.models.series import SeriesCatalog, SeriesEpisode, SeriesMetadata
from app.repositories.base import BaseRepository


class SeriesRepository(BaseRepository[SeriesCatalog]):
    def __init__(self, session: Session):
        super().__init__(SeriesCatalog, session)

    def get_with_metadata(self, series_id: str) -> Optional[dict]:
        stmt = (
            select(
                SeriesCatalog,
                SeriesMetadata.overview_es,
                SeriesMetadata.overview_en,
                SeriesMetadata.vote_average,
                SeriesMetadata.vote_count,
                SeriesMetadata.genres,
                SeriesMetadata.backdrop_path,
                SeriesMetadata.poster_path.label("tmdb_poster_path"),
                SeriesMetadata.tagline,
                SeriesMetadata.tmdb_id.label("metadata_tmdb_id"),
                SeriesMetadata.title.label("tmdb_title"),
                SeriesMetadata.release_date,
                SeriesMetadata.popularity,
                SeriesMetadata.status,
            )
            .outerjoin(SeriesMetadata, SeriesMetadata.tmdb_id == SeriesCatalog.tmdb_id)
            .where(
                or_(
                    SeriesCatalog.id.cast(str) == series_id,  # type: ignore[arg-type]
                    SeriesCatalog.tmdb_id == series_id,
                    SeriesCatalog.provider_id == series_id,
                )
            )
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        if not row:
            return None
        result = dict(row)
        # total_episodes and total_seasons
        episode_counts = self._get_episode_counts(result["id"])
        result.update(episode_counts)
        return result

    def _get_episode_counts(self, catalog_uuid: Any) -> dict:
        stmt = select(
            func.count(SeriesEpisode.id.distinct()).label("total_episodes"),
            func.count(SeriesEpisode.season_number.distinct()).label("total_seasons"),
        ).where(SeriesEpisode.catalog_id == catalog_uuid)
        row = self.session.execute(stmt).mappings().first()
        return dict(row) if row else {"total_episodes": 0, "total_seasons": 0}

    def get_by_key(self, series_key: str) -> Optional[dict]:
        stmt = (
            select(SeriesCatalog, SeriesMetadata)
            .outerjoin(SeriesMetadata, SeriesMetadata.tmdb_id == SeriesCatalog.tmdb_id)
            .where(SeriesCatalog.series_key == series_key)
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        return dict(row) if row else None

    def get_episodes_with_streams(
        self, catalog_id: str, page: int = 1, page_size: int = 100
    ) -> Tuple[List[dict], int, List[int]]:
        offset = (page - 1) * page_size

        count_stmt = (
            select(func.count())
            .select_from(SeriesEpisode)
            .where(SeriesEpisode.catalog_id == catalog_id)
        )
        total = self.session.execute(count_stmt).scalar() or 0

        seasons_stmt = (
            select(SeriesEpisode.season_number)
            .distinct()
            .where(SeriesEpisode.catalog_id == catalog_id)
            .order_by(SeriesEpisode.season_number)
        )
        seasons = [r[0] for r in self.session.execute(seasons_stmt).all()]

        data_sql = text("""
            WITH episode_streams AS (
                SELECT
                    se.id AS episode_id,
                    se.season_number,
                    se.episode_number,
                    se.numero,
                    jsonb_agg(
                        jsonb_build_object(
                            'url', ss.stream_url,
                            'label', COALESCE(ss.label, ss.country, 'Ver'),
                            'country', ss.country,
                            'provider_id', ss.provider_id,
                            'numero', ss.numero
                        ) ORDER BY
                            CASE WHEN ss.country = 'ES' THEN 0
                                 WHEN ss.country = 'EN' THEN 1
                                 WHEN ss.country = 'LATAM' THEN 2
                                 ELSE 3 END,
                            ss.numero ASC
                    ) AS stream_options
                FROM series_episodes se
                LEFT JOIN series_streams ss ON ss.episode_id = se.id
                WHERE se.catalog_id = :cid
                GROUP BY se.id, se.season_number, se.episode_number, se.numero
            )
            SELECT *
            FROM episode_streams
            ORDER BY season_number ASC, episode_number ASC
            LIMIT :limit OFFSET :offset
        """)
        items = [
            dict(r._mapping)
            for r in self.session.execute(
                data_sql, {"cid": catalog_id, "limit": page_size, "offset": offset}
            ).all()
        ]
        return items, total, seasons

    def search_by_provider_id(self, provider_id: str) -> Optional[SeriesCatalog]:
        stmt = select(SeriesCatalog).where(SeriesCatalog.provider_id == provider_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_title(self, title: str) -> Optional[dict]:
        import re

        stripped = re.sub(
            r"^[a-z]{2,5}\s*[-–]\s*", "", title, flags=re.IGNORECASE
        ).strip()
        stmt = (
            select(
                SeriesCatalog,
                SeriesMetadata.overview_es,
                SeriesMetadata.overview_en,
                SeriesMetadata.vote_average,
                SeriesMetadata.vote_count,
                SeriesMetadata.genres,
                SeriesMetadata.backdrop_path,
                SeriesMetadata.poster_path.label("tmdb_poster_path"),
                SeriesMetadata.title.label("tmdb_title"),
                SeriesMetadata.release_date,
                SeriesMetadata.popularity,
                SeriesMetadata.status,
            )
            .outerjoin(SeriesMetadata, SeriesMetadata.tmdb_id == SeriesCatalog.tmdb_id)
            .where(
                or_(
                    func.lower(func.trim(SeriesCatalog.title)).in_(
                        [title.lower().strip(), stripped.lower().strip()]
                    ),
                    func.lower(func.trim(SeriesCatalog.title)).like(
                        f"%{stripped.lower().strip()}%"
                    ),
                )
            )
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        return dict(row) if row else None

    def get_distinct_groups(self) -> List[str]:
        stmt = (
            select(SeriesCatalog.group_normalizado)
            .where(
                and_(
                    SeriesCatalog.group_normalizado.isnot(None),
                    SeriesCatalog.group_normalizado != "",
                )
            )
            .distinct()
            .order_by(SeriesCatalog.group_normalizado)
        )
        return [r[0] for r in self.session.execute(stmt).all()]

    def get_distinct_series_groups_catalog(
        self,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        upper_group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        year: Optional[int] = None,
    ) -> Dict[str, Any]:
        filters: List[str] = []
        params: Dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
        if group:
            filters.append(
                "(sc.group_normalizado ILIKE :group OR sc.title ILIKE :group)"
            )
            params["group"] = f"%{group}%"
        if upper_group:
            filters.append("UPPER(sc.group_normalizado) LIKE :upper_group")
            params["upper_group"] = f"%{upper_group}%"
        if country:
            filters.append("sc.country = :country")
            params["country"] = country
        if search:
            filters.append("(sc.title ILIKE :search OR sc.title ILIKE :search)")
            params["search"] = f"%{search}%"
        if year:
            filters.append("sc.year = :year")
            params["year"] = year
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        count_sql = f"SELECT COUNT(DISTINCT sc.id) AS total FROM series_catalog sc {where_clause}"
        total = self.session.execute(text(count_sql), params).scalar() or 0

        data_sql = f"""
            SELECT sc.id, sc.title, sc.series_key, sc.tmdb_id, sc.year, sc.country,
                sc.group_normalizado, sc.logo, sc.provider_id,
                sm.overview_es, sm.overview_en, sm.vote_average, sm.vote_count,
                sm.genres, sm.backdrop_path, sm.poster_path,
                sm.title AS tmdb_title, sm.release_date, sm.popularity, sm.status,
                COALESCE(
                    (SELECT COUNT(DISTINCT se.id) FROM series_episodes se WHERE se.catalog_id = sc.id),
                    0
                ) AS total_episodes,
                COALESCE(
                    (SELECT COUNT(DISTINCT se.season_number) FROM series_episodes se WHERE se.catalog_id = sc.id),
                    0
                ) AS total_seasons
            FROM series_catalog sc
            LEFT JOIN series_metadata sm ON sm.tmdb_id = sc.tmdb_id
            {where_clause}
            ORDER BY sc.title ASC
            LIMIT :limit OFFSET :offset
        """
        rows = self.session.execute(text(data_sql), params).mappings().all()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
