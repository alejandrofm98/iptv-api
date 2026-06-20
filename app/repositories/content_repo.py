from typing import Any

from sqlalchemy import String, and_, case, desc, func, literal_column, or_, select, text
from sqlalchemy.orm import Session

from app.models.content import MovieCatalog, MovieMetadata, MovieStream
from app.repositories.base import BaseRepository


class ContentRepository(BaseRepository[MovieCatalog]):
    def __init__(self, session: Session):
        super().__init__(MovieCatalog, session)

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

    def get_movie_with_metadata(self, movie_id: str) -> dict | None:
        stmt = (
            select(
                MovieCatalog,
                MovieMetadata.overview_es,
                MovieMetadata.overview_en,
                MovieMetadata.vote_average,
                MovieMetadata.vote_count,
                MovieMetadata.genres,
                MovieMetadata.backdrop_path,
                MovieMetadata.poster_path.label("tmdb_poster_path"),
                MovieMetadata.title.label("tmdb_title"),
                MovieMetadata.release_date,
                MovieMetadata.runtime_minutes,
                MovieMetadata.popularity,
                MovieMetadata.status,
                MovieMetadata.tagline,
            )
            .outerjoin(MovieMetadata, MovieMetadata.tmdb_id == MovieCatalog.tmdb_id)
            .where(
                or_(
                    MovieCatalog.id.cast(String) == movie_id,
                    MovieCatalog.tmdb_id == movie_id,
                    MovieCatalog.provider_id == movie_id,
                )
            )
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        if not row:
            return None
        result = self._flatten_row(dict(row))
        streams = self._get_movie_streams(result["id"])
        if streams:
            result["stream_options"] = streams
        return result

    def _get_movie_streams(self, movie_uuid: Any) -> list[dict]:
        stmt = (
            select(MovieStream)
            .where(MovieStream.movie_id == movie_uuid)
            .order_by(
                case(
                    (MovieStream.country == "ES", 0),
                    (MovieStream.country == "EN", 1),
                    (MovieStream.country == "LATAM", 2),
                    else_=3,
                ),
                MovieStream.numero,
            )
        )
        rows = self.session.execute(stmt).scalars().all()
        return [
            {
                "url": r.stream_url,
                "label": r.label or r.country or "Ver",
                "country": r.country,
                "quality": r.quality,
                "provider_id": r.provider_id,
                "numero": r.numero,
            }
            for r in rows
        ]

    def get_movies_paginated(
        self,
        page: int,
        page_size: int,
        country: str | None = None,
        search: str | None = None,
        year: int | None = None,
        genre: str | None = None,
        group: str | None = None,
    ) -> tuple[list[dict], int]:
        filters = []
        if country:
            filters.append(MovieCatalog.countries.any(country))
        if search:
            filters.append(
                or_(
                    MovieCatalog.title.ilike(f"%{search}%"),
                    MovieCatalog.nombre_dedup_key.ilike(f"%{search}%"),
                )
            )
        if year:
            filters.append(MovieCatalog.year == year)
        if genre:
            filters.append(text("movies_metadata.genres @> ARRAY[:genre]::text[]").bindparams(genre=genre))
        if group:
            filters.append(MovieCatalog.group_normalizado.ilike(f"%{group}%"))

        needs_metadata_join = genre is not None

        count_stmt = select(func.count()).select_from(MovieCatalog)
        if needs_metadata_join:
            count_stmt = count_stmt.outerjoin(MovieMetadata, MovieMetadata.tmdb_id == MovieCatalog.tmdb_id)
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = self.session.execute(count_stmt).scalar() or 0

        data_stmt = (
            select(
                MovieCatalog.id,
                MovieCatalog.title.label("nombre"),
                MovieCatalog.title.label("nombre_normalizado"),
                MovieCatalog.tmdb_id,
                MovieCatalog.year,
                MovieCatalog.countries,
                MovieCatalog.group_normalizado.label("grupo"),
                MovieCatalog.group_normalizado.label("grupo_normalizado"),
                MovieCatalog.logo,
                MovieCatalog.provider_id,
                MovieMetadata.poster_path.label("tmdb_poster_path"),
                MovieMetadata.backdrop_path,
                MovieMetadata.vote_average,
                MovieMetadata.vote_count,
                MovieMetadata.genres,
                MovieMetadata.overview_es,
                MovieMetadata.overview_en,
                MovieMetadata.release_date,
                MovieMetadata.popularity,
                MovieMetadata.status,
                MovieMetadata.tagline,
                MovieMetadata.title.label("tmdb_title"),
                literal_column("""(
                    SELECT COALESCE(
                        jsonb_agg(
                            jsonb_build_object(
                                'url', ms.stream_url,
                                'label', COALESCE(ms.label, ms.country, 'Ver'),
                                'country', ms.country,
                                'quality', ms.quality,
                                'provider_id', ms.provider_id,
                                'numero', ms.numero
                            ) ORDER BY
                                CASE WHEN ms.country = 'ES' THEN 0
                                     WHEN ms.country = 'EN' THEN 1
                                     WHEN ms.country = 'LATAM' THEN 2
                                     ELSE 3 END,
                                ms.numero ASC
                        ),
                        '[]'::jsonb
                    )
                    FROM movie_streams ms
                    WHERE ms.movie_id = movies_catalog.id
                )""").label("stream_options"),
                literal_column("""(
                    SELECT COUNT(ms.id) FROM movie_streams ms WHERE ms.movie_id = movies_catalog.id
                )""").label("stream_count"),
            )
            .outerjoin(MovieMetadata, MovieMetadata.tmdb_id == MovieCatalog.tmdb_id)
            .order_by(desc(MovieMetadata.release_date).nullslast(), MovieCatalog.title)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            data_stmt = data_stmt.where(and_(*filters))

        rows = self.session.execute(data_stmt).mappings().all()
        return [dict(r) for r in rows], total

    def get_distinct_groups(
        self, content_type: str, countries: list[str] | None = None
    ) -> list[str]:
        if content_type == "movies":
            table = MovieCatalog
            col = MovieCatalog.nombre_dedup_key
        elif content_type == "channels":
            from app.models.channel import Channel

            q = select(Channel.grupo).distinct().order_by(Channel.grupo)
            if countries:
                q = q.where(Channel.country.in_(countries))
            rows = self.session.execute(q).scalars().all()
            return [r for r in rows if r]
        else:
            from app.models.series import SeriesCatalog

            q = (
                select(SeriesCatalog.group_normalizado)
                .distinct()
                .order_by(SeriesCatalog.group_normalizado)
            )
            if countries:
                q = q.where(SeriesCatalog.countries.overlap(countries))
            rows = self.session.execute(q).scalars().all()
            return [r for r in rows if r]
        q = select(col).distinct().order_by(col)
        if countries:
            q = q.where(table.countries.overlap(countries))
        rows = self.session.execute(q).scalars().all()
        return [r for r in rows if r]

    def get_distinct_countries(self, content_type: str) -> list[str]:
        if content_type == "channels":
            from app.models.channel import Channel

            q = select(Channel.country).distinct().order_by(Channel.country)
            rows = self.session.execute(q).scalars().all()
            return [r for r in rows if r]
        if content_type == "movies":
            table = "movies_catalog"
        else:
            table = "series_catalog"
        q = text(f"SELECT DISTINCT unnest({table}.countries) AS c FROM {table} WHERE {table}.countries IS NOT NULL ORDER BY c")
        rows = self.session.execute(q).scalars().all()
        return [r for r in rows if r]

    def get_distinct_genres(self, content_type: str) -> list[str]:
        if content_type == "movies":
            from sqlalchemy import func as sa_func, distinct

            q = select(distinct(sa_func.unnest(MovieMetadata.genres))).order_by(
                sa_func.unnest(MovieMetadata.genres)
            )
        elif content_type == "series":
            from app.models.series import SeriesMetadata
            from sqlalchemy import func as sa_func, distinct

            q = select(distinct(sa_func.unnest(SeriesMetadata.genres))).order_by(
                sa_func.unnest(SeriesMetadata.genres)
            )
        else:
            return []
        rows = self.session.execute(q).scalars().all()
        return [r for r in rows if r]

    def search_by_provider_id(self, provider_id: str) -> MovieCatalog | None:
        stmt = select(MovieCatalog).where(MovieCatalog.provider_id == provider_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_movies_catalog_page(
        self,
        page: int,
        page_size: int,
        group: str | None = None,
        upper_group: str | None = None,
        country: str | None = None,
        search: str | None = None,
        year: int | None = None,
        genre: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        filters: list[str] = []
        params: dict[str, Any] = {"limit": page_size, "offset": (page - 1) * page_size}
        if group:
            filters.append("(mc.group_normalizado ILIKE :group OR mc.title ILIKE :group)")
            params["group"] = f"%{group}%"
        if upper_group:
            filters.append("UPPER(mc.group_normalizado) LIKE :upper_group")
            params["upper_group"] = f"%{upper_group}%"
        if country:
            filters.append(":country = ANY(mc.countries)")
            params["country"] = country
        if search:
            filters.append("(mc.title ILIKE :search OR mc.tmdb_id::text ILIKE :search)")
            params["search"] = f"%{search}%"
        if year:
            filters.append("mc.year = :year")
            params["year"] = year
        if genre:
            filters.append("mm.genres @> ARRAY[:genre]::text[]")
            params["genre"] = genre
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        count_join = "LEFT JOIN movies_metadata mm ON mm.tmdb_id = mc.tmdb_id" if genre else ""
        count_sql = f"SELECT COUNT(DISTINCT mc.tmdb_id) AS total FROM movies_catalog mc {count_join} {where_clause}"
        total = self.session.execute(text(count_sql), params).scalar() or 0

        data_sql = f"""
            SELECT DISTINCT ON (mc.tmdb_id)
                mc.id, mc.title, mc.tmdb_id, mc.year, mc.countries,
                mc.group_normalizado, mc.logo, mc.provider_id,
                mm.overview_es, mm.overview_en, mm.vote_average, mm.vote_count,
                mm.genres, mm.backdrop_path, mm.poster_path,
                mm.title AS tmdb_title, mm.release_date, mm.runtime_minutes,
                mm.popularity, mm.status, mm.tagline,
                COALESCE(
                    (
                        SELECT jsonb_agg(
                            jsonb_build_object(
                                'url', ms.stream_url,
                                'label', COALESCE(ms.label, ms.country, 'Ver'),
                                'country', ms.country,
                                'quality', ms.quality,
                                'provider_id', ms.provider_id,
                                'numero', ms.numero
                            ) ORDER BY
                                CASE WHEN ms.country = 'ES' THEN 0
                                     WHEN ms.country = 'EN' THEN 1
                                     WHEN ms.country = 'LATAM' THEN 2
                                     ELSE 3 END,
                                ms.numero ASC
                        )
                        FROM movie_streams ms
                        WHERE ms.movie_id = mc.id
                    ),
                    '[]'::jsonb
                ) AS stream_options,
                (
                    SELECT COUNT(ms.id) FROM movie_streams ms WHERE ms.movie_id = mc.id
                ) AS stream_count
            FROM movies_catalog mc
            LEFT JOIN movies_metadata mm ON mm.tmdb_id = mc.tmdb_id
            {where_clause}
            ORDER BY mc.tmdb_id NULLS LAST, mc.created_at DESC
            LIMIT :limit OFFSET :offset
        """
        rows = self.session.execute(text(data_sql), params).mappings().all()
        return [dict(r) for r in rows], total
