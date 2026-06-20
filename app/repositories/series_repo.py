from typing import Any

from sqlalchemy import Integer, String, and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.models.series import SeriesCatalog, SeriesEpisode, SeriesMetadata, SeriesStream
from app.repositories.base import BaseRepository


class SeriesRepository(BaseRepository[SeriesCatalog]):
    def __init__(self, session: Session):
        super().__init__(SeriesCatalog, session)

    def _flatten_row(self, row: dict) -> dict:
        """Extrae atributos de la entidad ORM y los mezcla con columnas individuales."""
        result = {}
        for key, value in row.items():
            if hasattr(value, "__table__"):
                for col in value.__table__.columns.keys():
                    result[col] = getattr(value, col)
            else:
                result[key] = value
        return result

    def get_with_metadata(self, series_id: str) -> dict | None:
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
                    SeriesCatalog.id.cast(String) == series_id,
                    SeriesCatalog.tmdb_id == series_id,
                    SeriesCatalog.provider_id == series_id,
                )
            )
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        if not row:
            return None
        result = self._flatten_row(dict(row))
        # total_episodes and total_seasons
        episode_counts = self._get_episode_counts(result["id"])
        result.update(episode_counts)
        return result

    def get_catalog_by_episode_provider_id(self, episode_provider_id: str) -> dict | None:
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
                SeriesMetadata.title.label("tmdb_title"),
                SeriesMetadata.release_date,
                SeriesMetadata.popularity,
                SeriesMetadata.status,
            )
            .outerjoin(SeriesMetadata, SeriesMetadata.tmdb_id == SeriesCatalog.tmdb_id)
            .join(SeriesEpisode, SeriesEpisode.catalog_id == SeriesCatalog.id)
            .join(SeriesStream, SeriesStream.episode_id == SeriesEpisode.id)
            .where(SeriesStream.provider_id == episode_provider_id)
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        if not row:
            return None
        result = self._flatten_row(dict(row))
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

    def get_by_key(self, series_key: str) -> dict | None:
        stmt = (
            select(SeriesCatalog, SeriesMetadata)
            .outerjoin(SeriesMetadata, SeriesMetadata.tmdb_id == SeriesCatalog.tmdb_id)
            .where(SeriesCatalog.series_key == series_key)
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        return self._flatten_row(dict(row)) if row else None

    def get_episodes_with_streams(
        self, catalog_id: str, page: int = 1, page_size: int = 100
    ) -> tuple[list[dict], int, list[int]]:
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
                    se.title,
                    se.title_en,
                    se.overview,
                    se.overview_en,
                    se.air_date,
                    se.still_path,
                    se.runtime,
                    se.vote_average,
                    se.vote_count,
                    se.episode_type,
                    se.tmdb_checked,
                    jsonb_agg(
                        jsonb_build_object(
                            'url', ss.stream_url,
                            'label', COALESCE(ss.label, ss.country, 'Ver'),
                            'country', ss.country,
                            'quality', ss.quality,
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
                GROUP BY se.id, se.season_number, se.episode_number, se.numero,
                         se.title, se.title_en, se.overview, se.overview_en,
                         se.air_date, se.still_path, se.runtime, se.vote_average,
                         se.vote_count, se.episode_type, se.tmdb_checked
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

    def search_by_provider_id(self, provider_id: str) -> SeriesCatalog | None:
        stmt = select(SeriesCatalog).where(SeriesCatalog.provider_id == provider_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_catalog_by_provider_id(self, provider_id: str) -> dict | None:
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
            .where(SeriesCatalog.provider_id == provider_id)
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        return self._flatten_row(dict(row)) if row else None

    def get_by_title(self, title: str) -> dict | None:
        import re

        stripped = re.sub(r"^[a-z]{2,5}\s*[-–]\s*", "", title, flags=re.IGNORECASE).strip()
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
        return self._flatten_row(dict(row)) if row else None

    def find_canonical_by_title(self, title: str) -> dict | None:
        """Find a series catalog entry by title that HAS tmdb_id (canonical twin)."""
        if not title:
            return None
        import re

        _STOP_WORDS = {
            "the", "los", "las", "les", "der", "die", "das", "les", "el", "la",
            "le", "un", "una", "uno", "unos", "unas", "de", "del", "des", "du",
            "do", "da", "di", "al", "an", "en", "and", "or", "for", "with",
        }
        stripped = re.sub(r"^[a-z]{2,5}\s*[-–]\s*", "", title, flags=re.IGNORECASE).strip()
        words = [
            w for w in re.split(r"\W+", stripped.lower())
            if len(w) > 2 and w not in _STOP_WORDS
        ]
        if not words:
            return None
        like_conditions = [
            func.lower(func.trim(SeriesCatalog.title)).like(f"%{w}%") for w in words
        ]
        match_score = sum(
            func.cast(
                func.lower(func.trim(SeriesCatalog.title)).like(f"%{w}%"),
                Integer,
            )
            for w in words
        )
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
                and_(
                    SeriesCatalog.tmdb_id.isnot(None),
                    SeriesCatalog.tmdb_id != "",
                    or_(*like_conditions),
                )
            )
            .order_by(match_score.desc(), func.length(SeriesCatalog.title).asc())
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        return self._flatten_row(dict(row)) if row else None

    def get_distinct_groups(self) -> list[str]:
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
        group: str | None = None,
        upper_group: str | None = None,
        country: str | None = None,
        search: str | None = None,
        year: int | None = None,
        genre: str | None = None,
    ) -> dict[str, Any]:
        filters: list[str] = []
        params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
        if group:
            filters.append("(sc.group_normalizado ILIKE :group OR sc.title ILIKE :group)")
            params["group"] = f"%{group}%"
        if upper_group:
            filters.append("UPPER(sc.group_normalizado) LIKE :upper_group")
            params["upper_group"] = f"%{upper_group}%"
        if country:
            filters.append(":country = ANY(sc.countries)")
            params["country"] = country
        if search:
            filters.append("(sc.title ILIKE :search OR sc.title ILIKE :search)")
            params["search"] = f"%{search}%"
        if year:
            filters.append("sc.year = :year")
            params["year"] = year
        if genre:
            filters.append("sm.genres @> ARRAY[:genre]::text[]")
            params["genre"] = genre

        has_streams_filter = (
            "EXISTS (SELECT 1 FROM series_episodes se JOIN series_streams ss ON ss.episode_id = se.id WHERE se.catalog_id = sc.id)"
        )
        base_where = f"{' AND '.join(filters)}" if filters else "TRUE"
        where_clause = f"WHERE {base_where} AND {has_streams_filter}"

        count_join = "LEFT JOIN series_metadata sm ON sm.tmdb_id = sc.tmdb_id" if genre else ""
        count_sql = f"SELECT COUNT(DISTINCT COALESCE(sc.tmdb_id, sc.id::text)) AS total FROM series_catalog sc {count_join} {where_clause}"
        total = self.session.execute(text(count_sql), params).scalar() or 0

        data_sql = f"""
            SELECT * FROM (
                SELECT DISTINCT ON (COALESCE(sc.tmdb_id, sc.id::text))
                    sc.id, sc.title, sc.series_key, sc.tmdb_id, sc.year, sc.countries,
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
                ORDER BY COALESCE(sc.tmdb_id, sc.id::text) NULLS LAST, sc.created_at DESC
            ) sub
            ORDER BY sub.release_date DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        """
        rows = self.session.execute(text(data_sql), params).mappings().all()
        return {
            "items": [dict(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
