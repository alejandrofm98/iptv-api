"""
Servicio de generación de playlists M3U dinámicas
"""
import os
import hashlib
from pathlib import Path
from typing import Optional, List, Dict, Any, Literal
from urllib.parse import urlparse
from supabase import Client

from utils.config import get_settings
import utils.constants as CONSTANTS

ContentType = Literal['full', 'live', 'movie', 'series']


class PlaylistService:
    """Servicio para generación de playlists M3U"""

    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.settings = get_settings()
        self._templates: Dict[str, Optional[str]] = {
            'full': None,
            'live': None,
            'movie': None,
            'series': None
        }
        self._m3u_dir = self._get_m3u_dir()
        self._load_templates()

    def _get_m3u_dir(self) -> str:
        is_docker = (
            os.path.exists(CONSTANTS.DOCKER_ENV_PATH) or
            os.getenv(CONSTANTS.DOCKER_ENV_FLAG) == CONSTANTS.DOCKER_ENV_VALUE
        )

        if os.getenv(CONSTANTS.M3U_DIR_ENV):
            return os.getenv(CONSTANTS.M3U_DIR_ENV)
        elif is_docker:
            return CONSTANTS.M3U_DIR_DOCKER
        else:
            project_root = Path(__file__).parent.parent
            return str(project_root / CONSTANTS.M3U_DIR_LOCAL_DEFAULT)

    def _load_templates(self):
        """Carga todos los templates M3U en memoria"""
        template_files = {
            'full': 'playlist_template.m3u',
            'live': 'playlist_template_live.m3u',
            'movie': 'playlist_template_movie.m3u',
            'series': 'playlist_template_series.m3u'
        }
        
        for key, filename in template_files.items():
            path = os.path.join(self._m3u_dir, filename)
            try:
                if os.path.exists(path):
                    with open(path, 'r', encoding='utf-8') as f:
                        self._templates[key] = f.read()
                    size_mb = len(self._templates[key]) / 1024 / 1024
                    print(f"✅ Template '{key}' cargado: {size_mb:.2f} MB")
                else:
                    print(f"⚠️  Template '{key}' no encontrado: {path}")
            except Exception as e:
                print(f"❌ Error cargando template '{key}': {e}")

    def reload_template(self):
        """Recarga todos los templates"""
        self._load_templates()

    def _extract_stream_id(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:16]

    def _build_proxy_url(
        self,
        original_url: str,
        username: str,
        password: str,
        content_type: str = 'live'
    ) -> str:
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

    def generate_m3u(self, username: str, password: str, content_type: ContentType = 'full') -> str:
        """
        Genera playlist M3U usando template pre-procesado.
        
        Args:
            username: Nombre de usuario
            password: Contraseña
            content_type: Tipo de contenido ('full', 'live', 'movie', 'series')
        
        Returns:
            Contenido M3U como string
        """
        public_domain = self.settings.public_domain.rstrip('/')
        
        template = self._templates.get(content_type) or self._templates.get('full')
        
        if template is None:
            self._load_templates()
            template = self._templates.get(content_type) or self._templates.get('full')
            
            if template is None:
                return "#EXTM3U\n#EXTINF:-1,Error\n# Error: No se encontró el archivo template.\n"

        content = template
        content = content.replace('{{DOMAIN}}', public_domain)
        content = content.replace('{{USERNAME}}', username)
        content = content.replace('{{PASSWORD}}', password)

        return content

    def _build_extinf(self, item: Dict[str, Any], content_type: str) -> str:
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
        channels = self.supabase.table('channels').select('id', count='exact').execute()
        movies = self.supabase.table('movies').select('id', count='exact').execute()
        series = self.supabase.table('series').select('id', count='exact').execute()

        return {
            'total_channels': channels.count or 0,
            'total_movies': movies.count or 0,
            'total_series': series.count or 0
        }

    def get_available_groups(self) -> List[str]:
        result = self.supabase.table('channels').select('grupo').execute()
        groups = set()

        for item in (result.data or []):
            if item.get('grupo'):
                groups.add(item['grupo'])

        return sorted(list(groups))

    def get_available_countries(self) -> List[str]:
        result = self.supabase.table('channels').select('country').execute()
        countries = set()

        for item in (result.data or []):
            if item.get('country'):
                countries.add(item['country'])

        return sorted(list(countries))