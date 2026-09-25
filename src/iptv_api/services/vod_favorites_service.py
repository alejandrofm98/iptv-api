"""Account-scoped VOD library backed by the shared PostgreSQL catalog."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from iptv_api.services.content_service import ContentServiceV2


class VodFavoritesService:
    def __init__(self, session: Session, content_service: ContentServiceV2):
        self.session = session
        self.content_service = content_service

    def list_items(self, user_id: str, username: str = "") -> list[dict[str, Any]]:
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT content_type, content_id, created_at
                    FROM vod_favorites
                    WHERE user_id = :user_id
                    ORDER BY created_at DESC
                    LIMIT 100
                    """
                ),
                {"user_id": user_id},
            )
            .mappings()
            .all()
        )
        result = []
        for favorite in rows:
            item = self._load_catalog_item(
                str(favorite["content_type"]), str(favorite["content_id"]), username
            )
            if item is None:
                continue
            result.append(
                {
                    "content_type": favorite["content_type"],
                    "content_id": favorite["content_id"],
                    "created_at": favorite["created_at"],
                    "item": item,
                }
            )
        return result

    def add_item(self, user_id: str, content_type: str, content_id: str) -> bool:
        result = self.session.execute(
            text(
                """
                INSERT INTO vod_favorites (user_id, content_type, content_id)
                VALUES (:user_id, :content_type, :content_id)
                ON CONFLICT (user_id, content_type, content_id) DO NOTHING
                RETURNING content_id
                """
            ),
            {"user_id": user_id, "content_type": content_type, "content_id": content_id},
        )
        self.session.flush()
        return result.scalar_one_or_none() is not None

    def remove_item(self, user_id: str, content_type: str, content_id: str) -> bool:
        result = self.session.execute(
            text(
                """
                DELETE FROM vod_favorites
                WHERE user_id = :user_id
                  AND content_type = :content_type
                  AND content_id = :content_id
                RETURNING content_id
                """
            ),
            {"user_id": user_id, "content_type": content_type, "content_id": content_id},
        )
        self.session.flush()
        return result.scalar_one_or_none() is not None

    def _load_catalog_item(
        self, content_type: str, content_id: str, username: str
    ) -> dict[str, Any] | None:
        if content_type == "movies":
            row = self.content_service.content_repo.get_movie_with_metadata(content_id)
            if row is None:
                return None
            row["stream_options"] = []
            return self.content_service._to_android_movie_from_catalog(
                row, username=username, allow_english_overview=True
            )

        row = self.content_service.series_repo.get_with_metadata(content_id)
        if row is None:
            return None
        return self.content_service._to_android_series_group_item(row, username=username)
