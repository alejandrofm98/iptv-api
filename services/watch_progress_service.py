"""
Servicio de progreso de visualización
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from supabase import Client


class WatchProgressService:
    """Servicio para CRUD de progreso de visualización"""

    def __init__(self, supabase: Client):
        self.supabase = supabase

    def get_continue_watching(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Obtiene items con progreso incompleto (entre 5% y 95%)"""
        result = self.supabase.table('watch_progress').select('*').eq(
            'user_id', user_id
        ).gt(
            'position_ms', 0
        ).order(
            'last_watched_at', desc=True
        ).limit(limit).execute()

        if not result.data:
            return []

        # Filtrar en memoria: progreso entre 5% y 95%
        incomplete = []
        for item in result.data:
            duration = item.get('duration_ms', 0)
            position = item.get('position_ms', 0)
            if duration > 0:
                progress = position / duration
                if 0.05 < progress < 0.95:
                    incomplete.append(item)
        return incomplete

    def get_progress(self, user_id: str, content_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene el progreso de un item específico"""
        result = self.supabase.table('watch_progress').select('*').eq(
            'user_id', user_id
        ).eq(
            'content_id', content_id
        ).execute()

        return result.data[0] if result.data else None

    def upsert_progress(self, user_id: str, content_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Crea o actualiza el progreso de visualización"""
        payload = {
            'user_id': user_id,
            'content_id': content_id,
            'content_type': data['content_type'],
            'position_ms': data['position_ms'],
            'duration_ms': data['duration_ms'],
            'series_name': data.get('series_name'),
            'season_number': data.get('season_number'),
            'episode_number': data.get('episode_number'),
            'title': data.get('title', ''),
            'image_url': data.get('image_url', ''),
            'last_watched_at': datetime.utcnow().isoformat() + 'Z',
        }

        result = self.supabase.table('watch_progress').upsert(
            payload,
            on_conflict='user_id,content_id'
        ).execute()

        return result.data[0] if result.data else payload

    def delete_progress(self, user_id: str, content_id: str) -> bool:
        """Elimina el progreso de un item"""
        result = self.supabase.table('watch_progress').delete().eq(
            'user_id', user_id
        ).eq(
            'content_id', content_id
        ).execute()

        return len(result.data) > 0 if result.data else False
