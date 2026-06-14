"""Playlist Service v2 — M3U generation from templates."""

import os
from pathlib import Path
from typing import Literal

import utils.constants as CONSTANTS
from utils.config import get_settings

ContentType = Literal["full", "live", "movie", "series"]


class PlaylistServiceV2:
    def __init__(self):
        self.settings = get_settings()
        self._templates: dict[str, str | None] = {
            "full": None,
            "live": None,
            "movie": None,
            "series": None,
        }
        self._m3u_dir = self._get_m3u_dir()
        self._load_templates()

    def _get_m3u_dir(self) -> str:
        is_docker = (
            os.path.exists(CONSTANTS.DOCKER_ENV_PATH)
            or os.getenv(CONSTANTS.DOCKER_ENV_FLAG) == CONSTANTS.DOCKER_ENV_VALUE
        )
        if os.getenv(CONSTANTS.M3U_DIR_ENV):
            return os.getenv(CONSTANTS.M3U_DIR_ENV) or ""
        elif is_docker:
            return CONSTANTS.M3U_DIR_DOCKER
        else:
            project_root = Path(__file__).parent.parent.parent
            return str(project_root / CONSTANTS.M3U_DIR_LOCAL_DEFAULT)

    def _load_templates(self):
        template_files = {
            "full": "playlist_template.m3u",
            "live": "playlist_template_live.m3u",
            "movie": "playlist_template_movie.m3u",
            "series": "playlist_template_series.m3u",
        }
        for key, filename in template_files.items():
            path = os.path.join(self._m3u_dir, filename)
            try:
                if os.path.exists(path):
                    with open(path, "r", encoding="utf-8") as f:
                        self._templates[key] = f.read()
            except Exception as e:
                print(f"Error cargando template '{key}': {e}")

    def reload_template(self):
        self._load_templates()

    def get_playlist_stats(self) -> dict[str, int]:
        return {
            "total_channels": 0,
            "total_movies": 0,
            "total_series": 0,
        }

    def generate_m3u(self, username: str, password: str, content_type: ContentType = "full") -> str:
        public_domain = self.settings.public_domain.rstrip("/")
        template = self._templates.get(content_type) or self._templates.get("full")
        if template is None:
            self._load_templates()
            template = self._templates.get(content_type) or self._templates.get("full")
            if template is None:
                return "#EXTM3U\n#EXTINF:-1,Error\n# Error: No se encontró el archivo template.\n"
        content = template
        content = content.replace("{{DOMAIN}}", public_domain)
        content = content.replace("{{USERNAME}}", username)
        content = content.replace("{{PASSWORD}}", password)
        while content.startswith("#EXTM3U\n#EXTM3U"):
            content = content.replace("#EXTM3U\n#EXTM3U", "#EXTM3U", 1)
        return content
