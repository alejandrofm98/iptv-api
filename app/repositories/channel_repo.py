from typing import Optional, List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_, and_, text

from app.models.channel import Channel
from app.repositories.base import BaseRepository


class ChannelRepository(BaseRepository[Channel]):
    def __init__(self, session: Session):
        super().__init__(Channel, session)

    def get_by_provider_id(self, provider_id: str) -> Optional[Channel]:
        stmt = select(Channel).where(Channel.provider_id == provider_id).limit(1)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_all(self) -> List[Channel]:
        stmt = select(Channel).order_by(Channel.nombre)
        return list(self.session.execute(stmt).scalars().all())

    def get_by_provider_ids(self, provider_ids: List[str]) -> List[Channel]:
        stmt = select(Channel).where(
            Channel.provider_id.cast(str).in_(provider_ids)
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_distinct_groups(self) -> List[str]:
        stmt = (
            select(func.distinct(
                func.coalesce(Channel.grupo_normalizado, Channel.grupo)
            ).label("grupo"))
            .where(
                and_(
                    Channel.grupo.isnot(None),
                    Channel.grupo != "",
                )
            )
            .order_by(text("1"))
        )
        return [r[0] for r in self.session.execute(stmt).all()]

    def get_distinct_countries(self) -> List[str]:
        stmt = (
            select(Channel.country)
            .where(
                and_(
                    Channel.country.isnot(None),
                    Channel.country != "",
                )
            )
            .distinct()
            .order_by(Channel.country)
        )
        return [r[0] for r in self.session.execute(stmt).all()]

    def get_paginated(
        self, page: int, page_size: int, country: Optional[str] = None,
        group: Optional[str] = None, search: Optional[str] = None,
    ) -> Tuple[List[Channel], int]:
        filters = []
        if country:
            filters.append(Channel.country == country)
        if group:
            filters.append(
                or_(
                    Channel.grupo_normalizado.ilike(f"%{group}%"),
                    Channel.grupo.ilike(f"%{group}%"),
                )
            )
        if search:
            filters.append(
                or_(
                    Channel.nombre_normalizado.ilike(f"%{search}%"),
                    Channel.nombre.ilike(f"%{search}%"),
                )
            )

        count_stmt = select(func.count()).select_from(Channel)
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = self.session.execute(count_stmt).scalar() or 0

        data_stmt = select(Channel)
        if filters:
            data_stmt = data_stmt.where(and_(*filters))
        data_stmt = data_stmt.order_by(Channel.numero).offset(
            (page - 1) * page_size
        ).limit(page_size)

        channels = list(self.session.execute(data_stmt).scalars().all())
        return channels, total
