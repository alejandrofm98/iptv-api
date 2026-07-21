from sqlalchemy import select
from sqlalchemy.orm import Session

from iptv_api.models.config import Config, SyncMetadata
from iptv_api.repositories.base import BaseRepository


class ConfigRepository(BaseRepository[Config]):
    def __init__(self, session: Session):
        super().__init__(Config, session)

    def get_value(self, key: str) -> str | None:
        stmt = select(Config.value).where(Config.key == key)
        row = self.session.execute(stmt).one_or_none()
        return row[0] if row else None

    def get_all(self) -> dict[str, str]:
        stmt = select(Config.key, Config.value)
        return {r.key: r.value for r in self.session.execute(stmt).all() if r.key}


class SyncMetadataRepository(BaseRepository[SyncMetadata]):
    def __init__(self, session: Session):
        super().__init__(SyncMetadata, session)

    def get_field(self, field_id: str) -> str | None:
        stmt = select(SyncMetadata).where(SyncMetadata.id == field_id).limit(1)
        row = self.session.execute(stmt).scalar_one_or_none()
        if not row:
            return None
        return str(getattr(row, field_id)) if hasattr(row, field_id) else None
