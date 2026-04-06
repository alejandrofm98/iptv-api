"""
Video Resolver Service - Resuelve URLs de embed a URLs directas usando yt-dlp
"""
import logging
import subprocess
import shutil
from typing import Optional

logger = logging.getLogger("iptv-api")


class VideoResolverService:
    """Resuelve URLs de hosting (embed) a URLs directas reproducibles usando yt-dlp."""

    def __init__(self):
        self.ytdlp_path = shutil.which("yt-dlp") or "yt-dlp"
        logger.info(f"VideoResolverService initialized with yt-dlp at: {self.ytdlp_path}")

    def resolve(self, embed_url: str) -> dict:
        """
        Resuelve una URL de embed a una URL directa reproducible.

        Args:
            embed_url: URL de embed del hosting (ej: https://mega.nz/embed/...)

        Returns:
            dict con 'success' (bool), 'url' (str si success), 'error' (str si falla)
        """
        if not embed_url or not embed_url.strip():
            return {"success": False, "error": "URL vacía"}

        embed_url = embed_url.strip()
        logger.info(f"Resolving embed URL: {embed_url}")

        try:
            result = subprocess.run(
                [
                    self.ytdlp_path,
                    "--get-url",
                    "--no-download",
                    "--no-check-certificates",
                    "--no-warnings",
                    "--user-agent", "Mozilla/5.0 (Linux; Android 14; Android TV) AppleWebKit/537.36",
                    embed_url,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0 and result.stdout.strip():
                direct_url = result.stdout.strip()
                logger.info(f"Successfully resolved: {embed_url[:50]}... -> {direct_url[:80]}...")
                return {"success": True, "url": direct_url}
            else:
                error_msg = result.stderr.strip()[:300] if result.stderr else "yt-dlp returned no output"
                logger.warning(f"Failed to resolve {embed_url[:50]}...: {error_msg}")
                return {"success": False, "error": error_msg}

        except subprocess.TimeoutExpired:
            logger.warning(f"Timeout resolving {embed_url[:50]}...")
            return {"success": False, "error": "Timeout (30s)"}
        except FileNotFoundError:
            logger.error("yt-dlp not found in PATH")
            return {"success": False, "error": "yt-dlp no instalado en el servidor"}
        except Exception as e:
            logger.error(f"Error resolving {embed_url[:50]}...: {e}")
            return {"success": False, "error": str(e)[:200]}

    def get_info(self, embed_url: str) -> dict:
        """
        Obtiene información del video sin descargar.

        Returns:
            dict con título, duración, formato, etc.
        """
        if not embed_url or not embed_url.strip():
            return {"success": False, "error": "URL vacía"}

        try:
            result = subprocess.run(
                [
                    self.ytdlp_path,
                    "--dump-json",
                    "--no-download",
                    "--no-check-certificates",
                    "--no-warnings",
                    embed_url.strip(),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode == 0 and result.stdout.strip():
                import json
                info = json.loads(result.stdout.strip())
                return {
                    "success": True,
                    "title": info.get("title", ""),
                    "duration": info.get("duration"),
                    "ext": info.get("ext", ""),
                    "url": info.get("url", ""),
                }
            else:
                return {"success": False, "error": "No se pudo obtener info"}

        except Exception as e:
            return {"success": False, "error": str(e)[:200]}
