from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    def __init__(self, model: type[ModelType], session: Session):
        self.model = model
        self.session = session

    def get_by_id(self, id_value: Any) -> ModelType | None:
        return self.session.get(self.model, id_value)

    def list_all(self, limit: int = 100, offset: int = 0) -> list[ModelType]:
        stmt = select(self.model).limit(limit).offset(offset)
        return list(self.session.execute(stmt).scalars().all())

    def count(self) -> int:
        stmt = select(func.count()).select_from(self.model)
        return self.session.execute(stmt).scalar() or 0

    def add(self, instance: ModelType) -> ModelType:
        self.session.add(instance)
        self.session.flush()
        return instance

    def delete(self, instance: ModelType) -> None:
        self.session.delete(instance)
        self.session.flush()
