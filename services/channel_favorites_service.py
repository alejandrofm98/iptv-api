"""Servicio de favoritos de canales por usuario."""

from typing import Any, Dict, List, Optional

from supabase import Client

from .postgres_service import get_postgres_service


class ChannelFavoritesService:
    """CRUD y listados de favoritos de canales."""

    def __init__(self, supabase: Client):
        self.supabase = supabase

    def list_favorites(self, user_id: str) -> List[Dict[str, Any]]:
        result = (
            self.supabase.table("channel_favorites")
            .select("user_id,channel_provider_id,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .execute()
        )
        return [self._normalize_row(row) for row in (result.data or [])]

    def add_favorite(self, user_id: str, channel_provider_id: str) -> Dict[str, Any]:
        payload = {
            "user_id": user_id,
            "channel_provider_id": str(channel_provider_id),
        }
        result = (
            self.supabase.table("channel_favorites")
            .upsert(payload, on_conflict="user_id,channel_provider_id")
            .execute()
        )
        row = result.data[0] if result.data else payload
        return self._normalize_row(row)

    def remove_favorite(self, user_id: str, channel_provider_id: str) -> bool:
        result = (
            self.supabase.table("channel_favorites")
            .delete()
            .eq("user_id", user_id)
            .eq("channel_provider_id", str(channel_provider_id))
            .execute()
        )
        return bool(result.data)

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
        if not provider_ids:
            return content_svc.build_paginated_payload([], 0, page, page_size)

        pg_service = get_postgres_service()
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
        rows = pg_service.execute_query(sql, tuple(params))

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
