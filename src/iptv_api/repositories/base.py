from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from iptv_api.database import Base

ModelType = TypeVar("ModelType", bound=Base)

_ID_PREFIXES = (
    "content:",
    "movie:",
    "series:content:",
    "series:provider:",
    "series:name:",
    "replay:",
)


def strip_id_prefix(raw_id: str) -> str:
    """Quita prefijos de agrupacion (content:, series:content:, replay:...) que
    usan los clientes como identificadores, dejando el id crudo del catalogo."""
    for prefix in _ID_PREFIXES:
        if raw_id.startswith(prefix):
            return raw_id[len(prefix) :]
    return raw_id


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
