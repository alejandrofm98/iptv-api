"""Servicio de progreso de visualizacion."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from supabase import Client


class WatchProgressService:
    """Servicio para CRUD de progreso de visualizacion."""

    TABLE_BY_TYPE = {
        "movie": "movies",
        "series": "series",
    }

    def __init__(self, supabase: Client):
        self.supabase = supabase

    def get_continue_watching(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Obtiene items con progreso incompleto (entre 5% y 95%)."""
        result = (
            self.supabase.table("watch_progress")
            .select("*")
            .eq("user_id", user_id)
            .gt("position_ms", 0)
            .order("last_watched_at", desc=True)
            .limit(limit)
            .execute()
        )

        if not result.data:
            return []

        incomplete: List[Dict[str, Any]] = []
        for item in result.data:
            duration = item.get("duration_ms", 0) or 0
            position = item.get("position_ms", 0) or 0
            if duration <= 0:
                continue
            progress = position / duration
            if 0.05 < progress < 0.95:
                incomplete.append(self._normalize_progress_row(item))
        return incomplete

    def get_progress(self, user_id: str, content_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene el progreso de un item especifico."""
        rows = self._lookup_progress_rows(user_id, content_id)
        if not rows:
            return None
        return self._normalize_progress_row(rows[0])

    def upsert_progress(self, user_id: str, content_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Crea o actualiza el progreso de visualizacion usando content_id canonico."""
        canonical_content_id = self._canonical_content_id(data.get("content_type"), content_id)
        payload = {
            "user_id": user_id,
            "content_id": canonical_content_id,
            "content_type": data["content_type"],
            "position_ms": data["position_ms"],
            "duration_ms": data["duration_ms"],
            "series_name": data.get("series_name"),
            "season_number": data.get("season_number"),
            "episode_number": data.get("episode_number"),
            "title": data.get("title", ""),
            "image_url": data.get("image_url", ""),
            "last_watched_at": datetime.utcnow().isoformat() + "Z",
        }

        result = (
            self.supabase.table("watch_progress")
            .upsert(payload, on_conflict="user_id,content_id")
            .execute()
        )

        row = result.data[0] if result.data else payload
        return self._normalize_progress_row(row)

    def delete_progress(self, user_id: str, content_id: str) -> bool:
        """Elimina el progreso de un item, soportando IDs legacy."""
        rows = self._lookup_progress_rows(user_id, content_id)
        if not rows:
            return False

        deleted_any = False
        for row in rows:
            result = (
                self.supabase.table("watch_progress")
                .delete()
                .eq("user_id", user_id)
                .eq("content_id", row.get("content_id"))
                .execute()
            )
            deleted_any = deleted_any or bool(result.data)
        return deleted_any

    def _normalize_progress_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        content_type = row.get("content_type")
        lookup_id = self._lookup_id(row.get("content_id"))
        content_row = self._find_content_row(content_type, row.get("content_id"))

        canonical_content_id = str(content_row.get("provider_id") or lookup_id or row.get("content_id") or "")
        normalized = dict(row)
        normalized["content_id"] = canonical_content_id

        if content_row:
            normalized["title"] = content_row.get("nombre") or row.get("title") or ""
            normalized["image_url"] = content_row.get("logo") or row.get("image_url") or ""
            if content_type == "series":
                normalized["series_name"] = content_row.get("serie_name") or row.get("series_name")
                normalized["season_number"] = self._safe_int(content_row.get("temporada")) or row.get("season_number")
                normalized["episode_number"] = self._safe_int(content_row.get("episodio")) or row.get("episode_number")

        return normalized

    def _canonical_content_id(self, content_type: Optional[str], content_id: str) -> str:
        content_row = self._find_content_row(content_type, content_id)
        if content_row and content_row.get("provider_id"):
            return str(content_row["provider_id"])
        return self._lookup_id(content_id)

    def _lookup_progress_rows(self, user_id: str, content_id: str) -> List[Dict[str, Any]]:
        candidates = []
        for candidate in (content_id, self._lookup_id(content_id), f"movie:{self._lookup_id(content_id)}", f"series:{self._lookup_id(content_id)}"):
            if candidate and candidate not in candidates:
                candidates.append(candidate)

        rows: List[Dict[str, Any]] = []
        for candidate in candidates:
            result = (
                self.supabase.table("watch_progress")
                .select("*")
                .eq("user_id", user_id)
                .eq("content_id", candidate)
                .execute()
            )
            if result.data:
                rows.extend(result.data)
        unique: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            unique[str(row.get("id") or row.get("content_id"))] = row
        return list(unique.values())

    def _find_content_row(self, content_type: Optional[str], content_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not content_type or not content_id:
            return None
        table = self.TABLE_BY_TYPE.get(content_type)
        if not table:
            return None

        lookup_id = self._lookup_id(content_id)
        for field, value in (("provider_id", lookup_id), ("id", lookup_id), ("id", content_id)):
            if not value:
                continue
            result = self.supabase.table(table).select("*").eq(field, value).limit(1).execute()
            if result.data:
                return result.data[0]
        return None

    @staticmethod
    def _lookup_id(content_id: Optional[str]) -> str:
        value = str(content_id or "")
        return value.split(":", 1)[1] if ":" in value else value

    @staticmethod
    def _safe_int(value: Any) -> Optional[int]:
        try:
            return int(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None
