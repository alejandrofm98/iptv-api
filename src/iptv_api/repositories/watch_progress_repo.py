from datetime import UTC, datetime
from typing import Any

from sqlalchemy import String, and_, delete, desc, func, select
from sqlalchemy.orm import Session

from iptv_api.models.content import MovieCatalog
from iptv_api.models.watch_progress import WatchProgress
from iptv_api.repositories.base import BaseRepository


class WatchProgressRepository(BaseRepository[WatchProgress]):
    def __init__(self, session: Session):
        super().__init__(WatchProgress, session)

    def get_by_user_and_content(
        self,
        user_id: str,
        content_id: str,
        season: int | None = None,
        episode: int | None = None,
    ) -> WatchProgress | None:
        stmt = select(WatchProgress).where(
            and_(
                WatchProgress.user_id == user_id,
                WatchProgress.content_id == content_id,
                func.coalesce(WatchProgress.season_number, 0) == func.coalesce(season, 0),
                func.coalesce(WatchProgress.episode_number, 0) == func.coalesce(episode, 0),
            )
        )
        return self.session.execute(stmt).scalars().first()

    def get_continue_watching(self, user_id: str, limit: int = 60) -> list[WatchProgress]:
        from sqlalchemy import text

        rows = self.session.execute(
            text(
                "SELECT DISTINCT ON (content_id, COALESCE(season_number, 0), COALESCE(episode_number, 0)) * "
                "FROM watch_progress "
                "WHERE user_id = :user_id AND position_ms > 0 AND is_watched = FALSE "
                "ORDER BY content_id, COALESCE(season_number, 0), COALESCE(episode_number, 0), last_watched_at DESC "
                "LIMIT :limit"
            ),
            {"user_id": user_id, "limit": limit},
        ).all()
        return [self._row_to_model(r) for r in rows]

    @staticmethod
    def _row_to_model(row) -> WatchProgress:
        return WatchProgress(
            id=row.id,
            user_id=row.user_id,
            content_id=row.content_id,
            content_type=row.content_type,
            position_ms=row.position_ms,
            duration_ms=row.duration_ms,
            series_name=row.series_name,
            season_number=row.season_number,
            episode_number=row.episode_number,
            title=row.title,
            image_url=row.image_url,
            last_watched_at=row.last_watched_at,
            is_watched=row.is_watched,
        )

    def get_watched_items(
        self, user_id: str, limit: int = 100, offset: int = 0
    ) -> list[WatchProgress]:
        stmt = (
            select(WatchProgress)
            .where(
                and_(
                    WatchProgress.user_id == user_id,
                    WatchProgress.is_watched.is_(True),
                )
            )
            .order_by(desc(WatchProgress.last_watched_at))
            .limit(limit)
            .offset(offset)
        )
        return list(self.session.execute(stmt).scalars().all())

    def count_watched_items(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(WatchProgress)
            .where(
                and_(
                    WatchProgress.user_id == user_id,
                    WatchProgress.is_watched.is_(True),
                )
            )
        )
        return int(self.session.execute(stmt).scalar_one())

    def upsert(self, user_id: str, content_id: str, data: dict[str, Any]) -> WatchProgress:
        existing = self.get_by_user_and_content(
            user_id,
            content_id,
            season=data.get("season_number"),
            episode=data.get("episode_number"),
        )
        if existing:
            for key, value in data.items():
                setattr(existing, key, value)
            self.session.flush()
            return existing
        wp = WatchProgress(user_id=user_id, content_id=content_id, **data)
        self.session.add(wp)
        self.session.flush()
        return wp

    def delete_by_user_and_content_id(self, user_id: str, content_id: str) -> bool:
        stmt = delete(WatchProgress).where(
            and_(
                WatchProgress.user_id == user_id,
                WatchProgress.content_id == content_id,
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount > 0

    def delete_episode(self, user_id: str, content_id: str, season: int, episode: int) -> bool:
        stmt = delete(WatchProgress).where(
            and_(
                WatchProgress.user_id == user_id,
                WatchProgress.content_id == content_id,
                WatchProgress.season_number == season,
                WatchProgress.episode_number == episode,
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount > 0

    def mark_watched(
        self,
        user_id: str,
        content_id: str,
        is_watched: bool,
        season: int | None = None,
        episode: int | None = None,
        content_type: str | None = None,
    ) -> WatchProgress | None:
        wp = self.get_by_user_and_content(user_id, content_id, season=season, episode=episode)
        if wp:
            wp.is_watched = is_watched  # type: ignore[assignment]
            self.session.flush()
            return wp
        if not is_watched:
            return None
        # No row exists — create one with is_watched=True
        wp = WatchProgress(
            user_id=user_id,
            content_id=content_id,
            content_type=content_type or "movie",
            is_watched=True,
            position_ms=0,
            duration_ms=0,
            title="",
            image_url="",
            season_number=season,
            episode_number=episode,
            last_watched_at=datetime.now(UTC),
        )
        self.session.add(wp)
        self.session.flush()
        return wp

    def get_all_for_user_and_series(
        self, user_id: str, series_name: str | None = None, catalog_id: str | None = None
    ) -> list[WatchProgress]:
        """Todas las filas de watch_progress de un usuario para una serie dada."""
        conditions = [
            WatchProgress.user_id == user_id,
            WatchProgress.content_type == "series",
        ]
        if catalog_id:
            conditions.append(WatchProgress.content_id == catalog_id)
        elif series_name:
            conditions.append(WatchProgress.series_name == series_name)
        else:
            return []
        stmt = (
            select(WatchProgress)
            .where(and_(*conditions))
            .order_by(
                desc(WatchProgress.season_number).nullslast(),
                desc(WatchProgress.episode_number).nullslast(),
            )
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_series_last_episode(self, user_id: str, series_name: str) -> WatchProgress | None:
        stmt = (
            select(WatchProgress)
            .where(
                and_(
                    WatchProgress.user_id == user_id,
                    WatchProgress.series_name == series_name,
                    WatchProgress.content_type == "series",
                )
            )
            .order_by(
                desc(WatchProgress.season_number).nullslast(),
                desc(WatchProgress.episode_number).nullslast(),
            )
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_watched_movie_content_ids(self, user_id: str) -> set[str]:
        """Content IDs y provider IDs de peliculas marcadas como vistas por el usuario.

        Los content_ids guardados son los IDs canonicos del catalogo (UUID).
        El home compara contra provider_id, asi que resolvemos cada content_id
        a su provider_id para que la coincidencia funcione.
        """
        stmt = (
            select(WatchProgress.content_id)
            .where(
                and_(
                    WatchProgress.user_id == user_id,
                    WatchProgress.content_type == "movie",
                    WatchProgress.is_watched.is_(True),
                )
            )
        )
        content_ids = {str(row) for row in self.session.execute(stmt).scalars().all()}
        if not content_ids:
            return set()

        provider_stmt = select(MovieCatalog.provider_id).where(
            MovieCatalog.id.cast(String).in_(list(content_ids))
        )
        provider_ids = {str(row) for row in self.session.execute(provider_stmt).scalars().all()}
        return content_ids | provider_ids

    def get_watched_series_names(self, user_id: str) -> set[str]:
        """Nombres de series con al menos un episodio marcado como visto.

        Consistente con la lista de "vistas": si la serie aparece ahi, la
        tarjeta del home muestra el tick aunque queden episodios por ver.
        """
        watched_stmt = (
            select(WatchProgress.series_name)
            .where(
                and_(
                    WatchProgress.user_id == user_id,
                    WatchProgress.content_type == "series",
                    WatchProgress.is_watched.is_(True),
                    WatchProgress.series_name.isnot(None),
                )
            )
            .distinct()
        )
        return {str(r) for r in self.session.execute(watched_stmt).scalars().all()}
