"""Servicio de favoritos de canales por usuario."""

import logging
from typing import Any, Dict, List, Optional

from .postgres_service import PostgresService

log = logging.getLogger(__name__)


class ChannelFavoritesService:
    """CRUD y listados de favoritos de canales."""

    def __init__(self, pg_service: PostgresService):
        self.pg = pg_service

    def list_favorites(self, user_id: str) -> List[Dict[str, Any]]:
        rows = self.pg.list_favorites(user_id)
        data = [self._normalize_row(row) for row in rows]
        log.info("list_favorites: user=%s returned %d rows", user_id, len(data))
        return data

    def add_favorite(self, user_id: str, channel_provider_id: str) -> Dict[str, Any]:
        row = self.pg.add_favorite(user_id, str(channel_provider_id))
        return self._normalize_row(row)

    def remove_favorite(self, user_id: str, channel_provider_id: str) -> bool:
        return self.pg.remove_favorite(user_id, str(channel_provider_id))

    def get_favorite_channels(
        self,
        user_id: str,
        content_svc,
        page: int = 1,
        page_size: int = 50,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = "",
        password: str = "",
    ) -> Dict[str, Any]:
        favorites = self.list_favorites(user_id)
        provider_ids = [item["channel_provider_id"] for item in favorites if item.get("channel_provider_id")]
        log.info("get_favorite_channels: user=%s provider_ids=%d country=%s", user_id, len(provider_ids), country)
        if not provider_ids:
            log.info("get_favorite_channels: user=%s has no favorites, returning empty", user_id)
            return content_svc.build_paginated_payload([], 0, page, page_size)

        filters = ["provider_id IS NOT NULL", "provider_id::text = ANY(%s)"]
        params: List[Any] = [provider_ids]

        if country:
            filters.append("country = %s")
            params.append(country)

        if search:
            filters.append("(nombre_normalizado ILIKE %s OR nombre ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        sql = f"""
            SELECT *
            FROM channels
            WHERE {' AND '.join(filters)}
        """
        rows = self.pg.execute_query(sql, tuple(params))
        log.info("get_favorite_channels: user=%s channels_query returned %d rows (country=%s)", user_id, len(rows), country)

        favorite_order = {provider_id: index for index, provider_id in enumerate(provider_ids)}
        rows.sort(key=lambda row: favorite_order.get(str(row.get("provider_id") or ""), len(provider_ids)))

        total = len(rows)
        offset = (page - 1) * page_size
        page_rows = rows[offset:offset + page_size]
        items = [
            content_svc.to_android_catalog_item(row, "channels", username=username, password=password)
            for row in page_rows
        ]
        return content_svc.build_paginated_payload(items, total, page, page_size)

    @staticmethod
    def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
        provider_id = str(row.get("channel_provider_id") or "")
        return {
            "user_id": row.get("user_id"),
            "channel_provider_id": provider_id,
            "provider_id": provider_id,
            "created_at": row.get("created_at"),
        }
