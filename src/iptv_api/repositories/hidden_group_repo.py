from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from iptv_api.models.channel import HiddenChannelGroup
from iptv_api.repositories.base import BaseRepository


class HiddenGroupRepository(BaseRepository[HiddenChannelGroup]):
    def __init__(self, session: Session):
        super().__init__(HiddenChannelGroup, session)

    def list_by_user(self, user_id: str) -> list[HiddenChannelGroup]:
        stmt = (
            select(HiddenChannelGroup)
            .where(HiddenChannelGroup.user_id == user_id)
            .order_by(HiddenChannelGroup.group_name)
        )
        return list(self.session.execute(stmt).scalars().all())

    def hide(self, user_id: str, country: str, group_name: str) -> HiddenChannelGroup:
        existing = self.session.execute(
            select(HiddenChannelGroup).where(
                and_(
                    HiddenChannelGroup.user_id == user_id,
                    HiddenChannelGroup.country == country,
                    HiddenChannelGroup.group_name == group_name,
                )
            )
        ).scalars().first()
        if existing is not None:
            return existing
        row = HiddenChannelGroup(user_id=user_id, country=country, group_name=group_name)
        self.session.add(row)
        self.session.flush()
        return row

    def unhide(self, user_id: str, country: str, group_name: str) -> bool:
        stmt = delete(HiddenChannelGroup).where(
            and_(
                HiddenChannelGroup.user_id == user_id,
                HiddenChannelGroup.country == country,
                HiddenChannelGroup.group_name == group_name,
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount > 0
