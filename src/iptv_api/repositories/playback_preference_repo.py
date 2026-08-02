"""Persistencia de preferencias de reproduccion."""

from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from iptv_api.models.playback_preference import PlaybackPreference
from iptv_api.repositories.base import BaseRepository


class PlaybackPreferenceRepository(BaseRepository[PlaybackPreference]):
    def __init__(self, session: Session):
        super().__init__(PlaybackPreference, session)

    def get(self, user_id: str, content_type: str, catalog_id: UUID) -> PlaybackPreference | None:
        stmt = select(PlaybackPreference).where(
            and_(
                PlaybackPreference.user_id == user_id,
                PlaybackPreference.content_type == content_type,
                PlaybackPreference.catalog_id == catalog_id,
            )
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def upsert(
        self, user_id: str, content_type: str, catalog_id: UUID, data: dict[str, Any]
    ) -> PlaybackPreference:
        row = self.get(user_id, content_type, catalog_id)
        if row is None:
            row = PlaybackPreference(
                user_id=user_id,
                content_type=content_type,
                catalog_id=catalog_id,
            )
            self.session.add(row)
        for key, value in data.items():
            setattr(row, key, value)
        self.session.flush()
        return row

    def delete_for_content(self, user_id: str, content_type: str, catalog_id: UUID) -> bool:
        stmt = delete(PlaybackPreference).where(
            and_(
                PlaybackPreference.user_id == user_id,
                PlaybackPreference.content_type == content_type,
                PlaybackPreference.catalog_id == catalog_id,
            )
        )
        result = self.session.execute(stmt)
        self.session.flush()
        return bool(getattr(result, "rowcount", 0) > 0)
