"""Preferencias sincronizadas de audio y subtitulos."""

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from iptv_api.core.exceptions import BadRequestException, NotFoundException
from iptv_api.repositories.content_repo import ContentRepository
from iptv_api.repositories.playback_preference_repo import PlaybackPreferenceRepository
from iptv_api.repositories.series_repo import SeriesRepository


class PlaybackPreferenceService:
    def __init__(self, session: Session):
        self.repo = PlaybackPreferenceRepository(session)
        self.content_repo = ContentRepository(session)
        self.series_repo = SeriesRepository(session)

    def get(self, user_id: str, content_type: str, catalog_id: str) -> dict[str, Any] | None:
        canonical = self.resolve_catalog_id(content_type, catalog_id)
        row = self.repo.get(user_id, content_type, canonical)
        return self._normalize(row) if row else None

    def upsert(
        self, user_id: str, content_type: str, catalog_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        canonical = self.resolve_catalog_id(content_type, catalog_id)
        row = self.repo.upsert(user_id, content_type, canonical, data)
        return self._normalize(row)

    def delete(self, user_id: str, content_type: str, catalog_id: str) -> bool:
        canonical = self.resolve_catalog_id(content_type, catalog_id)
        return self.repo.delete_for_content(user_id, content_type, canonical)

    def delete_canonical(self, user_id: str, content_type: str, catalog_id: UUID) -> bool:
        return self.repo.delete_for_content(user_id, content_type, catalog_id)

    def resolve_catalog_id(self, content_type: str, identifier: str) -> UUID:
        self._validate_content_type(content_type)
        row = (
            self.content_repo.get_movie_with_metadata(identifier)
            if content_type == "movie"
            else self.series_repo.get_with_metadata(identifier)
        )
        if not row and content_type == "series":
            row = self.series_repo.get_by_key(identifier)
        if not row and content_type == "series":
            row = self.series_repo.find_canonical_by_title(identifier)
        if not row and content_type == "series":
            row = self.series_repo.get_catalog_by_episode_provider_id(identifier)
            if not row:
                row = self.series_repo.get_catalog_by_episode_id(identifier)
        if not row or not row.get("id"):
            raise NotFoundException("Catalogo", identifier)
        return UUID(str(row["id"]))

    @staticmethod
    def _validate_content_type(content_type: str) -> None:
        if content_type not in {"movie", "series"}:
            raise BadRequestException("content_type debe ser 'movie' o 'series'")

    @staticmethod
    def _normalize(row) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "content_type": row.content_type,
            "catalog_id": str(row.catalog_id),
            "audio_language": row.audio_language,
            "audio_label": row.audio_label,
            "subtitle_language": row.subtitle_language,
            "subtitle_label": row.subtitle_label,
            "subtitles_disabled": row.subtitles_disabled,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
