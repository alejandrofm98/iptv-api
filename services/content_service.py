"""
Servicio de gestión de contenido (canales, películas, series)
"""
from typing import Optional, List, Dict, Any
from supabase import Client

from utils.config import get_settings


class ContentService:
    """Servicio para obtener contenido en formato JSON"""

    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.settings = get_settings()

    def _build_proxy_url(self, provider_id: str, username: str, password: str, content_type: str = 'live') -> str:
        """Construye la URL proxificada para el stream usando provider_id"""
        base_url = self.settings.public_domain.rstrip('/')
        return f"{base_url}/{content_type}/{username}/{password}/{provider_id}.ts"

    def _parse_channel(self, row: Dict[str, Any], username: str = '', password: str = '') -> Dict[str, Any]:
        """Parses a channel row from Supabase"""
        provider_id = row.get('provider_id', '')
        return {
            'id': provider_id,
            'num': row.get('numero'),
            'nombre': row.get('nombre'),
            'logo': row.get('logo'),
            'grupo': row.get('grupo'),
            'country': row.get('country'),
            'provider_id': provider_id,
            'tvg_id': row.get('tvg_id'),
            'url': row.get('url'),
            'stream_url': self._build_proxy_url(provider_id, username, password, 'live') if provider_id and username and password else None
        }

    def _parse_movie(self, row: Dict[str, Any], username: str = '', password: str = '') -> Dict[str, Any]:
        """Parses a movie row from Supabase"""
        provider_id = row.get('provider_id', '')
        return {
            'id': provider_id,
            'num': row.get('numero'),
            'nombre': row.get('nombre'),
            'logo': row.get('logo'),
            'grupo': row.get('grupo'),
            'country': row.get('country'),
            'provider_id': provider_id,
            'url': row.get('url'),
            'stream_url': self._build_proxy_url(provider_id, username, password, 'movie') if provider_id and username and password else None
        }

    def _parse_series(self, row: Dict[str, Any], username: str = '', password: str = '') -> Dict[str, Any]:
        """Parses a series row from Supabase"""
        provider_id = row.get('provider_id', '')
        return {
            'id': provider_id,
            'num': row.get('numero'),
            'nombre': row.get('nombre'),
            'logo': row.get('logo'),
            'grupo': row.get('grupo'),
            'country': row.get('country'),
            'provider_id': provider_id,
            'temporada': row.get('temporada'),
            'episodio': row.get('episodio'),
            'url': row.get('url'),
            'stream_url': self._build_proxy_url(provider_id, username, password, 'series') if provider_id and username and password else None
        }

    def get_channels(
        self,
        skip: int = 0,
        limit: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        username: str = '',
        password: str = ''
    ) -> Dict[str, Any]:
        """Obtiene lista paginada de canales"""
        query = self.supabase.table('channels').select('*', count='exact')

        if group:
            query = query.ilike('grupo', f'%{group}%')
        if country:
            query = query.eq('country', country)

        query = query.order('numero', desc=False)
        query = query.range(skip, skip + limit - 1)

        result = query.execute()

        return {
            'total': result.count or 0,
            'skip': skip,
            'limit': limit,
            'items': [self._parse_channel(row, username, password) for row in (result.data or [])]
        }

    def get_channel(self, channel_id: str, username: str = '', password: str = '') -> Optional[Dict[str, Any]]:
        """Obtiene un canal específico"""
        result = self.supabase.table('channels').select('*').eq('id', channel_id).execute()

        if result.data:
            return self._parse_channel(result.data[0], username, password)
        return None

    def get_movies(
        self,
        skip: int = 0,
        limit: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        username: str = '',
        password: str = ''
    ) -> Dict[str, Any]:
        """Obtiene lista paginada de películas"""
        query = self.supabase.table('movies').select('*', count='exact')

        if group:
            query = query.ilike('grupo', f'%{group}%')
        if country:
            query = query.eq('country', country)

        query = query.order('numero', desc=False)
        query = query.range(skip, skip + limit - 1)

        result = query.execute()

        return {
            'total': result.count or 0,
            'skip': skip,
            'limit': limit,
            'items': [self._parse_movie(row, username, password) for row in (result.data or [])]
        }

    def get_movie(self, movie_id: str, username: str = '', password: str = '') -> Optional[Dict[str, Any]]:
        """Obtiene una película específica"""
        result = self.supabase.table('movies').select('*').eq('id', movie_id).execute()

        if result.data:
            return self._parse_movie(result.data[0], username, password)
        return None

    def get_series(
        self,
        skip: int = 0,
        limit: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        username: str = '',
        password: str = ''
    ) -> Dict[str, Any]:
        """Obtiene lista paginada de series"""
        query = self.supabase.table('series').select('*', count='exact')

        if group:
            query = query.ilike('grupo', f'%{group}%')
        if country:
            query = query.eq('country', country)

        query = query.order('numero', desc=False)
        query = query.range(skip, skip + limit - 1)

        result = query.execute()

        return {
            'total': result.count or 0,
            'skip': skip,
            'limit': limit,
            'items': [self._parse_series(row, username, password) for row in (result.data or [])]
        }

    def get_serie(self, series_id: str, username: str = '', password: str = '') -> Optional[Dict[str, Any]]:
        """Obtiene una serie específica"""
        result = self.supabase.table('series').select('*').eq('id', series_id).execute()

        if result.data:
            return self._parse_series(result.data[0], username, password)
        return None

    def get_groups(self, content_type: str = 'channels') -> List[str]:
        """Obtiene lista de grupos disponibles"""
        table = 'channels' if content_type == 'channels' else 'movies' if content_type == 'movies' else 'series'
        column = 'grupo'

        result = self.supabase.table(table).select(column).execute()
        groups = set()

        for item in (result.data or []):
            if item.get(column):
                groups.add(item[column])

        return sorted(list(groups))

    def get_countries(self, content_type: str = 'channels') -> List[str]:
        """Obtiene lista de países disponibles"""
        table = 'channels' if content_type == 'channels' else 'movies' if content_type == 'movies' else 'series'
        column = 'country'

        result = self.supabase.table(table).select(column).execute()
        countries = set()

        for item in (result.data or []):
            if item.get(column):
                countries.add(item[column])

        return sorted(list(countries))
