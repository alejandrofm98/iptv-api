from typing import Any

from sqlalchemy import and_, delete, desc, select
from sqlalchemy.orm import Session

from app.models.watch_progress import WatchProgress
from app.repositories.base import BaseRepository


class WatchProgressRepository(BaseRepository[WatchProgress]):
    def __init__(self, session: Session):
        super().__init__(WatchProgress, session)

    def get_by_user_and_content(
        self, user_id: str, content_id: str,
        season: int | None = None, episode: int | None = None,
    ) -> WatchProgress | None:
        stmt = select(WatchProgress).where(
            and_(
                WatchProgress.user_id == user_id,
                WatchProgress.content_id == content_id,
                WatchProgress.season_number == (season if season is not None else 0),
                WatchProgress.episode_number == (episode if episode is not None else 0),
            )
        )
        return self.session.execute(stmt).scalars().first()

    def get_continue_watching(self, user_id: str, limit: int = 60) -> list[WatchProgress]:
        stmt = (
            select(WatchProgress)
            .where(
                and_(
                    WatchProgress.user_id == user_id,
                    WatchProgress.position_ms > 0,
                    WatchProgress.is_watched.is_(False),
                )
            )
            .order_by(desc(WatchProgress.last_watched_at))
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_watched_items(self, user_id: str, limit: int = 100) -> list[WatchProgress]:
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
        )
        return list(self.session.execute(stmt).scalars().all())

    def upsert(self, user_id: str, content_id: str, data: dict[str, Any]) -> WatchProgress:
        existing = self.get_by_user_and_content(
            user_id, content_id,
            season=data.get("season_number"), episode=data.get("episode_number"),
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

    def mark_watched(self, user_id: str, content_id: str, is_watched: bool) -> WatchProgress | None:
        wp = self.get_by_user_and_content(user_id, content_id)
        if wp:
            wp.is_watched = is_watched  # type: ignore[assignment]
            self.session.flush()
        return wp

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
