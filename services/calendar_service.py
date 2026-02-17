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
