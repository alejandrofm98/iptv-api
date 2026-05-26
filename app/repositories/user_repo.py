from typing import List, Optional

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    def __init__(self, session: Session):
        super().__init__(User, session)

    def get_by_username(self, username: str) -> Optional[User]:
        stmt = select(User).where(User.username == username)
        return self.session.execute(stmt).scalar_one_or_none()

    def get_by_id(self, user_id: str) -> Optional[User]:
        stmt = select(User).where(User.id == user_id)
        return self.session.execute(stmt).scalar_one_or_none()

    def count_active_sessions(self, user_id: str) -> int:
        from app.models.user import ActiveSession

        stmt = (
            select(func.count())
            .select_from(ActiveSession)
            .where(ActiveSession.user_id == user_id)
        )
        return self.session.execute(stmt).scalar() or 0

    def list_paginated(self, page: int, page_size: int) -> tuple[List[User], int]:
        total = self.count()
        stmt = (
            select(User)
            .order_by(desc(User.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        users = list(self.session.execute(stmt).scalars().all())
        return users, total

    def create(self, username: str, password_hash: str, **kwargs) -> User:
        user = User(username=username, password_hash=password_hash, **kwargs)
        self.session.add(user)
        self.session.flush()
        return user
