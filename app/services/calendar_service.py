"""Calendar Service v2 — uses SQLAlchemy session for stored procedures and ORM."""

from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.channel import Channel


class CalendarServiceV2:
    def __init__(self, session: Session):
        self.session = session
        self._provider_id_cache: dict[str, str | None] = {}

    def get_events_by_date(self, fecha: date) -> list[dict[str, Any]]:
        sql = text("SELECT * FROM get_eventos_fecha_con_channels(:fecha)")
        rows = self.session.execute(sql, {"fecha": fecha}).mappings().all()
        return [self._convert_dates(dict(r)) for r in rows]

    def get_event_by_id(self, event_id: str) -> dict[str, Any] | None:
        sql = text("SELECT * FROM get_evento_con_channels(:event_id)")
        row = self.session.execute(sql, {"event_id": event_id}).mappings().first()
        if row:
            return self._convert_dates(dict(row))
        return None

    def get_provider_ids(self, channel_ids: list[str]) -> dict[str, str]:
        if not channel_ids:
            return {}
        missing = [cid for cid in channel_ids if cid not in self._provider_id_cache]
        if missing:
            stmt = select(Channel.id, Channel.provider_id).where(Channel.id.in_(missing))
            try:
                rows = self.session.execute(stmt).all()
                for row in rows:
                    self._provider_id_cache[str(row.id)] = row.provider_id
                for cid in missing:
                    if cid not in self._provider_id_cache:
                        self._provider_id_cache[cid] = None
            except Exception:
                for cid in missing:
                    self._provider_id_cache[cid] = None
        return {cid: self._provider_id_cache.get(cid) or "" for cid in channel_ids}

    @staticmethod
    def _convert_dates(evento: dict) -> dict:
        if isinstance(evento.get("fecha"), date):
            evento["fecha"] = evento["fecha"].isoformat()
        return evento
