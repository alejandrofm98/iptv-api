from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.orm import Session

from app.models.user import ActiveSession, User
from app.repositories.base import BaseRepository


class SessionRepository(BaseRepository[ActiveSession]):
    def __init__(self, session: Session):
        super().__init__(ActiveSession, session)

    def get_by_user(self, user_id: str) -> List[ActiveSession]:
        stmt = (
            select(ActiveSession)
            .where(ActiveSession.user_id == user_id)
            .order_by(desc(ActiveSession.last_activity))
        )
        return list(self.session.execute(stmt).scalars().all())

    def get_by_user_and_device(
        self, user_id: str, device_id: str
    ) -> Optional[ActiveSession]:
        stmt = select(ActiveSession).where(
            and_(
                ActiveSession.user_id == user_id,
                ActiveSession.device_id == device_id,
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert(self, user_id: str, device_id: str, data: dict) -> ActiveSession:
        session = self.get_by_user_and_device(user_id, device_id)
        if session:
            for key, value in data.items():
                setattr(session, key, value)
        else:
            session = ActiveSession(user_id=user_id, device_id=device_id, **data)
            self.session.add(session)
        self.session.flush()
        return session

    def count_by_user(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(ActiveSession)
            .where(ActiveSession.user_id == user_id)
        )
        return self.session.execute(stmt).scalar() or 0

    def delete_by_user_and_device(self, user_id: str, device_id: str) -> bool:
        stmt = delete(ActiveSession).where(
            and_(
                ActiveSession.user_id == user_id,
                ActiveSession.device_id == device_id,
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount > 0  # type: ignore[attr-defined]

    def delete_by_user(self, user_id: str) -> int:
        stmt = delete(ActiveSession).where(ActiveSession.user_id == user_id)
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount  # type: ignore[attr-defined]

    def cleanup_inactive(self, timeout_minutes: int = 30) -> int:
        cutoff = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        stmt = delete(ActiveSession).where(ActiveSession.last_activity < cutoff)
        result = self.session.execute(stmt)
        self.session.flush()
        return result.rowcount  # type: ignore[attr-defined]

    def list_all_with_users(self, limit: int = 100) -> List[dict]:
        stmt = (
            select(ActiveSession, User.username)
            .join(User, User.id == ActiveSession.user_id)
            .order_by(desc(ActiveSession.last_activity))
            .limit(limit)
        )
        rows = self.session.execute(stmt).mappings().all()
        return [dict(r) for r in rows]
