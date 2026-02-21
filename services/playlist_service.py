"""
Servicio de generación de playlists M3U dinámicas
"""
import os
import re
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Iterator
from urllib.parse import urlparse, urlencode
from supabase import Client

from utils.config import get_settings
import utils.constants as CONSTANTS


class PlaylistService:
    """Servicio para generación de playlists M3U"""

    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.settings = get_settings()
        self._template_cache = None
        self._template_path = self._get_template_path()
        self._load_template()

    def _get_template_path(self) -> str:
        """
        Determina la ruta del template M3U según el entorno (Docker/Local)
        """
        # Detectar si estamos en Docker
        is_docker = (
            os.path.exists(CONSTANTS.DOCKER_ENV_PATH) or
            os.getenv(CONSTANTS.DOCKER_ENV_FLAG) == CONSTANTS.DOCKER_ENV_VALUE
        )

        # Usar variable de entorno si está definida
        if os.getenv(CONSTANTS.M3U_DIR_ENV):
            m3u_dir = os.getenv(CONSTANTS.M3U_DIR_ENV)
        elif is_docker:
            m3u_dir = CONSTANTS.M3U_DIR_DOCKER
        else:
            # Modo local: ruta relativa al proyecto
            project_root = Path(__file__).parent.parent
            m3u_dir = str(project_root / CONSTANTS.M3U_DIR_LOCAL_DEFAULT)

        return os.path.join(m3u_dir, "playlist_template.m3u")

    def _load_template(self):
        """Carga el template M3U en memoria para acceso rápido"""
        try:
            if os.path.exists(self._template_path):
                with open(self._template_path, 'r', encoding='utf-8') as f:
                    self._template_cache = f.read()
                print(f"✅ Template M3U cargado en memoria: {len(self._template_cache):,} caracteres")
            else:
                print(f"⚠️  Template no encontrado: {self._template_path}")
                self._template_cache = None
        except Exception as e:
            print(f"❌ Error cargando template: {e}")
            self._template_cache = None

    def reload_template(self):
        """Recarga el template (útil después de sincronización)"""
        self._load_template()

    def _extract_stream_id(self, url: str) -> str:
        """
        Extrae un ID único del stream a partir de la URL original.
        Retorna un hash corto para usarlo como identificador.
        """
        return hashlib.md5(url.encode()).hexdigest()[:16]

    def _build_proxy_url(
        self,
        original_url: str,
        username: str,
        password: str,
        content_type: str = 'live'
    ) -> str:
        """
        Construye la URL proxificada para el stream.

        Args:
            original_url: URL original del stream
            username: Usuario IPTV
            password: Contraseña del usuario
            content_type: 'live', 'movie' o 'series'

        Returns:
            URL proxificada: http://domain/live/user/pass/stream_id.ts
        """
        stream_id = self._extract_stream_id(original_url)

        parsed = urlparse(original_url)
        path = parsed.path.lower()

        if '.m3u8' in path:
            ext = '.m3u8'
        elif '.ts' in path:
            ext = '.ts'
        else:
            ext = '.ts'

        base_url = self.settings.public_domain.rstrip('/')

        if content_type == 'live':
            return f"{base_url}/{username}/{password}/{stream_id}{ext}"
        else:
            return f"{base_url}/{content_type}/{username}/{password}/{stream_id}{ext}"

    def generate_m3u(
        self,
        username: str,
        password: str,
        include_channels: bool = True,
        include_movies: bool = True,
        include_series: bool = True,
        group_filter: Optional[str] = None,
        country_filter: Optional[str] = None
    ) -> str:
        """
        Genera playlist M3U usando template pre-procesado con placeholders.
        Ultra-rápido: usa template cacheado en memoria + string.replace()

        Args:
            username: Nombre de usuario del cliente
            password: Contraseña del cliente
            include_channels, include_movies, include_series: Mantenidos por compatibilidad
            group_filter, country_filter: Mantenidos por compatibilidad

        Returns:
            Contenido M3U completo como string
        """
        public_domain = self.settings.public_domain.rstrip('/')

        if self._template_cache is None:
            self._load_template()

            if self._template_cache is None:
                return "#EXTM3U\n#EXTINF:-1,Error\n# Error: No se encontró el archivo template. El contenido aún no ha sido sincronizado.\n"

        content = self._template_cache
        content = content.replace('{{DOMAIN}}', public_domain)
        content = content.replace('{{USERNAME}}', username)
        content = content.replace('{{PASSWORD}}', password)

        return content

    def generate_m3u_filtered(
        self,
        username: str,
        password: str,
        group_filter: Optional[str] = None,
        country_filter: Optional[str] = None,
        search_filter: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Iterator[str]:
        """
        Genera playlist M3U con filtrado real, procesando línea a línea.
        Usa streaming para no cargar todo en memoria — ideal para reproductores limitados.

        Si no hay ningún filtro activo, delega al método rápido generate_m3u().

        Args:
            username: Nombre de usuario del cliente
            password: Contraseña del cliente
            group_filter: Filtrar por group-title exacto (ej: "ES| ESPAÑOL")
            country_filter: Filtrar por país en group-title o tvg-id (ej: "ES")
            search_filter: Buscar substring en nombre de canal (case-insensitive)
            limit: Número máximo de entradas a devolver

        Yields:
            Fragmentos de texto del M3U filtrado
        """
        public_domain = self.settings.public_domain.rstrip('/')

        if self._template_cache is None:
            self._load_template()

        if self._template_cache is None:
            yield "#EXTM3U\n#EXTINF:-1,Error\n# Template no encontrado\n"
            return

        # Sin filtros: usar el replace rápido de siempre (evita iterar 2.6M líneas)
        if not any([group_filter, country_filter, search_filter, limit]):
            content = self._template_cache
            content = content.replace('{{DOMAIN}}', public_domain)
            content = content.replace('{{USERNAME}}', username)
            content = content.replace('{{PASSWORD}}', password)
            yield content
            return

        # Con filtros: procesar línea a línea
        yield "#EXTM3U\n"

        lines = self._template_cache.splitlines()
        count = 0
        i = 0

        # Saltar la primera línea #EXTM3U del template
        if lines and lines[0].startswith('#EXTM3U'):
            i = 1

        while i < len(lines):
            line = lines[i]

            if not line.startswith('#EXTINF'):
                i += 1
                continue

            url_line = lines[i + 1] if i + 1 < len(lines) else ''
            include = True

            # Filtro por group-title exacto
            if group_filter and f'group-title="{group_filter}"' not in line:
                include = False

            # Filtro por país (busca en toda la línea EXTINF, case-insensitive)
            if country_filter and include:
                if country_filter.upper() not in line.upper():
                    include = False

            # Filtro de búsqueda por nombre (el nombre está tras la última coma en EXTINF)
            if search_filter and include:
                if search_filter.lower() not in line.lower():
                    include = False

            if include:
                extinf = (
                    line
                    .replace('{{DOMAIN}}', public_domain)
                    .replace('{{USERNAME}}', username)
                    .replace('{{PASSWORD}}', password)
                )
                url = (
                    url_line
                    .replace('{{DOMAIN}}', public_domain)
                    .replace('{{USERNAME}}', username)
                    .replace('{{PASSWORD}}', password)
                )

                yield extinf + '\n'
                yield url + '\n'
                count += 1

                if limit and count >= limit:
                    break

            i += 2

    def _build_extinf(self, item: Dict[str, Any], content_type: str) -> str:
        """
        Construye la línea #EXTINF para un item.

        Args:
            item: Datos del canal/película/serie
            content_type: 'channel', 'movie' o 'series'

        Returns:
            Línea #EXTINF formateada
        """
        name = item.get('nombre', 'Unknown')
        logo = item.get('logo', '')
        group = item.get('grupo', '')
        tvg_id = item.get('tvg_id', '')

        attrs = []

        if tvg_id:
            attrs.append(f'tvg-id="{tvg_id}"')

        attrs.append(f'tvg-name="{name}"')

        if logo:
            attrs.append(f'tvg-logo="{logo}"')

        if group:
            attrs.append(f'group-title="{group}"')

        attrs_str = ' '.join(attrs)

        return f'#EXTINF:-1 {attrs_str},{name}'

    def get_playlist_stats(self) -> Dict[str, int]:
        """Obtiene estadísticas de contenido disponible"""
        channels = self.supabase.table('channels').select(
            'id', count='exact'
        ).execute()
        movies = self.supabase.table('movies').select(
            'id', count='exact'
        ).execute()
        series = self.supabase.table('series').select(
            'id', count='exact'
        ).execute()

        return {
            'total_channels': channels.count or 0,
            'total_movies': movies.count or 0,
            'total_series': series.count or 0
        }

    def get_available_groups(self) -> List[str]:
        """Obtiene lista de grupos disponibles"""
        result = self.supabase.table('channels').select('grupo').execute()
        groups = set()

        for item in (result.data or []):
            if item.get('grupo'):
                groups.add(item['grupo'])

        return sorted(list(groups))

    def get_available_countries(self) -> List[str]:
        """Obtiene lista de países disponibles"""
        result = self.supabase.table('channels').select('country').execute()
        countries = set()

        for item in (result.data or []):
            if item.get('country'):
                countries.add(item['country'])

        return sorted(list(countries))