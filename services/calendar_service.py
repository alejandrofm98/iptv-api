"""
Servicio para obtener eventos del calendario deportivo
"""
from datetime import date
from typing import List, Dict, Any, Optional
from services.postgres_service import PostgresService


class CalendarService:
    """
    Servicio para consultar eventos del calendario con canales resueltos.
    Usa PostgreSQL directamente para llamar a las funciones SQL definidas en el schema.
    """

    def __init__(self, pg_service: PostgresService):
        self.pg = pg_service
        self._provider_id_cache: Dict[str, Optional[str]] = {}

    def _convert_dates_to_strings(self, eventos: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Convierte objetos date a strings ISO en los eventos"""
        for evento in eventos:
            if isinstance(evento.get('fecha'), date):
                evento['fecha'] = evento['fecha'].isoformat()
        return eventos

    def get_events_by_date(self, fecha: date) -> List[Dict[str, Any]]:
        """
        Obtiene todos los eventos de una fecha específica con sus canales resueltos.

        Args:
            fecha: Fecha a consultar (formato: YYYY-MM-DD)

        Returns:
            Lista de eventos con información completa de canales
        """
        sql = """
            SELECT * FROM get_eventos_fecha_con_channels(%s)
        """
        results = self.pg.execute_query(sql, (fecha,))
        return self._convert_dates_to_strings(results)

    def get_provider_ids(self, channel_ids: List[str]) -> Dict[str, str]:
        """
        Dado una lista de channel_id (ids internos de la BD), devuelve un dict
        mapeando cada channel_id a su provider_id en la tabla de canales.
        """
        if not channel_ids:
            return {}
        # Filtrar solo los que no están en caché
        missing = [cid for cid in channel_ids if cid not in self._provider_id_cache]
        if missing:
            # PostgreSQL IN con placeholders
            placeholders = ','.join(['%s'] * len(missing))
            sql = f"SELECT id::text, provider_id::text FROM channels WHERE id::text IN ({placeholders})"
            try:
                rows = self.pg.execute_query(sql, tuple(missing))
                for row in rows:
                    self._provider_id_cache[str(row['id'])] = row.get('provider_id')
                # Marcar los que no se encontraron como None
                for cid in missing:
                    if cid not in self._provider_id_cache:
                        self._provider_id_cache[cid] = None
            except Exception:
                for cid in missing:
                    self._provider_id_cache[cid] = None
        return {cid: self._provider_id_cache.get(cid) for cid in channel_ids}

    def get_event_by_id(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene un evento específico por su ID con canales resueltos.

        Args:
            event_id: UUID del evento

        Returns:
            Evento con información de canales o None si no existe
        """
        sql = """
            SELECT * FROM get_evento_con_channels(%s)
        """
        results = self.pg.execute_query(sql, (event_id,))
        if results:
            converted = self._convert_dates_to_strings(results)
            return converted[0]
        return None
