from typing import Optional, List, Tuple, Any
from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_, and_, case, desc

from app.models.content import MovieCatalog, MovieMetadata, MovieStream
from app.repositories.base import BaseRepository


class ContentRepository(BaseRepository[MovieCatalog]):
    def __init__(self, session: Session):
        super().__init__(MovieCatalog, session)

    def get_movie_with_metadata(self, movie_id: str) -> Optional[dict]:
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
                    MovieCatalog.id.cast(str) == movie_id,
                    MovieCatalog.tmdb_id == movie_id,
                    MovieCatalog.provider_id == movie_id,
                )
            )
            .limit(1)
        )
        row = self.session.execute(stmt).mappings().first()
        if not row:
            return None
        result = dict(row)
        streams = self._get_movie_streams(result["id"])
        if streams:
            result["stream_options"] = streams
        return result

    def _get_movie_streams(self, movie_uuid: Any) -> List[dict]:
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
                "provider_id": r.provider_id,
                "numero": r.numero,
            }
            for r in rows
        ]

    def get_movies_paginated(
        self, page: int, page_size: int, country: Optional[str] = None,
        search: Optional[str] = None, year: Optional[int] = None,
    ) -> Tuple[List[dict], int]:
        filters = []
        if country:
            filters.append(MovieCatalog.country == country)
        if search:
            filters.append(
                or_(
                    MovieCatalog.title.ilike(f"%{search}%"),
                    MovieCatalog.nombre_dedup_key.ilike(f"%{search}%"),
                )
            )
        if year:
            filters.append(MovieCatalog.year == year)

        count_stmt = select(func.count()).select_from(MovieCatalog)
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = self.session.execute(count_stmt).scalar() or 0

        data_stmt = (
            select(
                MovieCatalog,
                MovieMetadata.poster_path.label("tmdb_poster_path"),
                MovieMetadata.backdrop_path,
                MovieMetadata.vote_average,
            )
            .outerjoin(MovieMetadata, MovieMetadata.tmdb_id == MovieCatalog.tmdb_id)
            .order_by(desc(MovieCatalog.year).nullslast(), MovieCatalog.title)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if filters:
            data_stmt = data_stmt.where(and_(*filters))

        rows = self.session.execute(data_stmt).mappings().all()
        return [dict(r) for r in rows], total

    def get_distinct_groups(self, content_type: str, countries: Optional[List[str]] = None) -> List[str]:
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
            q = select(SeriesCatalog.nombre_normalizado).distinct().order_by(SeriesCatalog.nombre_normalizado)
            if countries:
                q = q.where(SeriesCatalog.country.in_(countries))
            rows = self.session.execute(q).scalars().all()
            return [r for r in rows if r]
        q = select(col).distinct().order_by(col)
        if countries:
            q = q.where(table.country.in_(countries))
        rows = self.session.execute(q).scalars().all()
        return [r for r in rows if r]

    def get_distinct_countries(self, content_type: str) -> List[str]:
        if content_type == "movies":
            table = MovieCatalog
        elif content_type == "channels":
            from app.models.channel import Channel
            q = select(Channel.country).distinct().order_by(Channel.country)
            rows = self.session.execute(q).scalars().all()
            return [r for r in rows if r]
        else:
            from app.models.series import SeriesCatalog
            q = select(SeriesCatalog.country).distinct().order_by(SeriesCatalog.country)
            rows = self.session.execute(q).scalars().all()
            return [r for r in rows if r]
        q = select(table.country).distinct().order_by(table.country)
        rows = self.session.execute(q).scalars().all()
        return [r for r in rows if r]

    def search_by_provider_id(self, provider_id: str) -> Optional[MovieCatalog]:
        stmt = select(MovieCatalog).where(MovieCatalog.provider_id == provider_id)
        return self.session.execute(stmt).scalar_one_or_none()
