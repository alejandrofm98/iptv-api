"""
Servicio de generación de playlists M3U dinámicas
"""
import os
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse
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
        is_docker = (
            os.path.exists(CONSTANTS.DOCKER_ENV_PATH) or
            os.getenv(CONSTANTS.DOCKER_ENV_FLAG) == CONSTANTS.DOCKER_ENV_VALUE
        )

        if os.getenv(CONSTANTS.M3U_DIR_ENV):
            m3u_dir = os.getenv(CONSTANTS.M3U_DIR_ENV)
        elif is_docker:
            m3u_dir = CONSTANTS.M3U_DIR_DOCKER
        else:
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
        """Extrae un ID único del stream a partir de la URL original"""
        return hashlib.md5(url.encode()).hexdigest()[:16]

    def _build_proxy_url(
        self,
        original_url: str,
        username: str,
        password: str,
        content_type: str = 'live'
    ) -> str:
        """Construye la URL proxificada para el stream"""
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

    def generate_m3u(self, username: str, password: str) -> str:
        """
        Genera playlist M3U usando template pre-procesado con placeholders.
        Ultra-rápido: usa template cacheado en memoria + string.replace()

        Returns:
            Contenido M3U completo como string
        """
        public_domain = self.settings.public_domain.rstrip('/')

        if self._template_cache is None:
            self._load_template()

            if self._template_cache is None:
                return "#EXTM3U\n#EXTINF:-1,Error\n# Error: No se encontró el archivo template.\n"

        content = self._template_cache
        content = content.replace('{{DOMAIN}}', public_domain)
        content = content.replace('{{USERNAME}}', username)
        content = content.replace('{{PASSWORD}}', password)

        return content

    def _build_extinf(self, item: Dict[str, Any], content_type: str) -> str:
        """Construye la línea #EXTINF para un item"""
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
        channels = self.supabase.table('channels').select('id', count='exact').execute()
        movies = self.supabase.table('movies').select('id', count='exact').execute()
        series = self.supabase.table('series').select('id', count='exact').execute()

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