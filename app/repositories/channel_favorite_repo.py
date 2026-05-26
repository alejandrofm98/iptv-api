from typing import List

from sqlalchemy import and_, delete, desc, select
from sqlalchemy.orm import Session

from app.models.channel import ChannelFavorite
from app.repositories.base import BaseRepository


class ChannelFavoriteRepository(BaseRepository[ChannelFavorite]):
    def __init__(self, session: Session):
        super().__init__(ChannelFavorite, session)

    def list_by_user(self, user_id: str) -> List[ChannelFavorite]:
        stmt = (
            select(ChannelFavorite)
            .where(ChannelFavorite.user_id == user_id)
            .order_by(desc(ChannelFavorite.created_at))
        )
        return list(self.session.execute(stmt).scalars().all())

    def add(self, user_id: str, channel_provider_id: str) -> ChannelFavorite:
        fav = ChannelFavorite(user_id=user_id, channel_provider_id=channel_provider_id)
        self.session.add(fav)
        self.session.flush()
        return fav

    def remove(self, user_id: str, channel_provider_id: str) -> bool:
        stmt = delete(ChannelFavorite).where(
            and_(
                ChannelFavorite.user_id == user_id,
                ChannelFavorite.channel_provider_id == channel_provider_id,
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount > 0
