"""
Servicio de gestión de contenido (canales, películas, series)
"""
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
from supabase import Client

from utils.config import get_settings


class ContentService:
    """Servicio para obtener contenido en formato JSON"""

    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.settings = get_settings()

    def _extract_stream_id(self, url: str) -> tuple:
        """
        Extrae el ID y extensión del stream de la URL original.

        Args:
        Returns:
            (stream_id, extension, content_type)
        """
        if not url:
            return (None, None, 'live')

        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]

        if len(path_parts) >= 2:
            path_lower = parsed.path.lower()

            if '/live/' in path_lower:
                content_type = 'live'
            elif '/movie/' in path_lower:
                content_type = 'movie'
            elif '/series/' in path_lower:
                content_type = 'series'
            else:
                content_type = 'live'

            last_part = path_parts[-1] if path_parts else ''
            if '.' in last_part:
                parts = last_part.rsplit('.', 1)
                stream_id = parts[0]
                extension = parts[1] if len(parts) > 1 else 'ts'
            else:
                stream_id = last_part
                extension = 'ts'

            return (stream_id, extension, content_type)

        return (None, None, 'live')

    def _build_proxy_url(self, original_url: str, username: str, password: str) -> str:
        """
        Transforma la URL original al formato del proxy.

        Args:
            original_url: URL original ej: http://PROVIDER_URL/series/USER/PASS/1732159.mkv
            username: Usuario IPTV
            password: Password IPTV

        Returns:
            URL transformada ej: https://DOMAIN.com/series/user/pass/1732159.mkv
        """
        if not original_url:
            return ''

        stream_id, extension, content_type = self._extract_stream_id(original_url)

        if not stream_id:
            return ''

        base_url = self.settings.public_domain.rstrip('/')

        if content_type == 'live':
            return f"{base_url}/{content_type}/{username}/{password}/{stream_id}"

        return f"{base_url}/{content_type}/{username}/{password}/{stream_id}.{extension}" if extension else f"{base_url}/{content_type}/{username}/{password}/{stream_id}"

    def _parse_channel(self, row: Dict[str, Any], username: str = '', password: str = '') -> Dict[str, Any]:
        """Parses a channel row from Supabase"""
        original_url = row.get('url', '')
        stream_id, _, _ = self._extract_stream_id(original_url)

        return {
            'id': stream_id or '',
            'num': row.get('numero'),
            'nombre': row.get('nombre'),
            'logo': row.get('logo'),
            'grupo': row.get('grupo'),
            'country': row.get('country'),
            'provider_id': row.get('provider_id'),
            'tvg_id': row.get('tvg_id'),
            'url': original_url,
            'stream_url': self._build_proxy_url(original_url, username, password) if original_url and username and password else None
        }

    def _parse_movie(self, row: Dict[str, Any], username: str = '', password: str = '') -> Dict[str, Any]:
        """Parses a movie row from Supabase"""
        original_url = row.get('url', '')
        stream_id, _, _ = self._extract_stream_id(original_url)

        return {
            'id': stream_id or '',
            'num': row.get('numero'),
            'nombre': row.get('nombre'),
            'logo': row.get('logo'),
            'grupo': row.get('grupo'),
            'country': row.get('country'),
            'provider_id': row.get('provider_id'),
            'url': original_url,
            'stream_url': self._build_proxy_url(original_url, username, password) if original_url and username and password else None
        }

    def _parse_series(self, row: Dict[str, Any], username: str = '', password: str = '') -> Dict[str, Any]:
        """Parses a series row from Supabase"""
        original_url = row.get('url', '')
        stream_id, _, _ = self._extract_stream_id(original_url)

        return {
            'id': stream_id or '',
            'num': row.get('numero'),
            'nombre': row.get('nombre'),
            'logo': row.get('logo'),
            'grupo': row.get('grupo'),
            'country': row.get('country'),
            'provider_id': row.get('provider_id'),
            'temporada': row.get('temporada'),
            'episodio': row.get('episodio'),
            'url': original_url,
            'stream_url': self._build_proxy_url(original_url, username, password) if original_url and username and password else None
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

    def get_content_count(self) -> Dict[str, int]:
        """Obtiene el número total de canales, películas y series"""
        channels = self.supabase.table('channels').select('id', count='exact').execute()
        movies = self.supabase.table('movies').select('id', count='exact').execute()
        series = self.supabase.table('series').select('id', count='exact').execute()

        return {
            'channels': channels.count or 0,
            'movies': movies.count or 0,
            'series': series.count or 0
        }
