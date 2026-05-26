"""Calendar Service v2 — uses SQLAlchemy session for raw SQL."""

from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session


class CalendarServiceV2:
    def __init__(self, session: Session):
        self.session = session
        self._provider_id_cache: Dict[str, Optional[str]] = {}

    def get_events_by_date(self, fecha: date) -> List[Dict[str, Any]]:
        sql = text("SELECT * FROM get_eventos_fecha_con_channels(:fecha)")
        rows = self.session.execute(sql, {"fecha": fecha}).mappings().all()
        return [self._convert_dates(dict(r)) for r in rows]

    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        sql = text("SELECT * FROM get_evento_con_channels(:event_id)")
        row = self.session.execute(sql, {"event_id": event_id}).mappings().first()
        if row:
            return self._convert_dates(dict(row))
        return None

    def get_provider_ids(self, channel_ids: List[str]) -> Dict[str, str]:
        if not channel_ids:
            return {}
        missing = [cid for cid in channel_ids if cid not in self._provider_id_cache]
        if missing:
            placeholders = ",".join([f":cid{i}" for i in range(len(missing))])
            params = {f"cid{i}": cid for i, cid in enumerate(missing)}
            sql = text(
                f"SELECT id::text, provider_id::text FROM channels WHERE id::text IN ({placeholders})"
            )
            try:
                rows = self.session.execute(sql, params).mappings().all()
                for row in rows:
                    self._provider_id_cache[str(row["id"])] = row.get("provider_id")
                for cid in missing:
                    if cid not in self._provider_id_cache:
                        self._provider_id_cache[cid] = None
            except Exception:
                for cid in missing:
                    self._provider_id_cache[cid] = None
        return {cid: self._provider_id_cache.get(cid) for cid in channel_ids}

    @staticmethod
    def _convert_dates(evento: dict) -> dict:
        if isinstance(evento.get("fecha"), date):
            evento["fecha"] = evento["fecha"].isoformat()
        return evento
