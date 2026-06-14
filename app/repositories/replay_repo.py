from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.orm import Session

from app.models.replay import Replay
from app.repositories.base import BaseRepository


class ReplayRepository(BaseRepository[Replay]):
    def __init__(self, session: Session):
        super().__init__(Replay, session)

    def get_by_slug(self, slug: str) -> Replay | None:
        stmt = select(Replay).where(Replay.slug == slug)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_paginated(
        self,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> tuple[list[Replay], int]:
        filters = []
        if search:
            filters.append(
                or_(
                    Replay.title.ilike(f"%{search}%"),
                    Replay.event_name.ilike(f"%{search}%"),
                )
            )

        count_stmt = select(func.count()).select_from(Replay)
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = self.session.execute(count_stmt).scalar() or 0

        data_stmt = select(Replay)
        if filters:
            data_stmt = data_stmt.where(and_(*filters))
        data_stmt = (
            data_stmt.order_by(desc(Replay.event_date), desc(Replay.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )

        replays = list(self.session.execute(data_stmt).scalars().all())
        return replays, total
