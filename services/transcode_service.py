"""
Servicio de transcodificación de audio para streams IPTV
Convierte codecs de audio incompatibles con navegadores (E-AC-3, AC-3, etc.) a AAC
"""
import asyncio
import subprocess
import re
from typing import Optional, Tuple, AsyncIterator, Dict, Any
import hashlib
import time
import logging

import utils.constants as CONSTANTS

logger = logging.getLogger(__name__)


class TranscodeService:
    """
    Servicio para transcodificar audio de streams a formato compatible con navegadores.
    Usa FFmpeg para convertir E-AC-3/AC-3 a AAC sin transcodificar el video.
    """

    # Cache de detección de codec: url_hash -> (codec, timestamp)
    _codec_cache: Dict[str, Tuple[str, float]] = {}
    _CODEC_CACHE_TTL: float = 3600.0  # 1 hora

    # Codecs que requieren transcodificación
    UNSUPPORTED_CODECS = {'eac3', 'ac3', 'truehd', 'dts', 'flac'}

    # Codecs soportados por navegadores
    SUPPORTED_CODECS = {'aac', 'mp3', 'opus', 'vorbis', 'mp4a'}

    # URLs que ya sabemos que requieren transcode (cache persistente simple)
    _known_transcode_urls: set = set()

    def __init__(self):
        pass

    def _hash_url(self, url: str) -> str:
        """Genera hash corto del URL para cache"""
        return hashlib.md5(url.encode()).hexdigest()[:16]

    async def detect_audio_codec(self, url: str) -> Optional[str]:
        """
        Detecta el codec de audio del stream usando ffprobe.
        
        Returns:
            Nombre del codec de audio (e.g., 'eac3', 'aac') o None si no se puede detectar
        """
        url_hash = self._hash_url(url)
        cached = self._codec_cache.get(url_hash)
        
        if cached:
            codec, cached_at = cached
            if (time.time() - cached_at) < self._CODEC_CACHE_TTL:
                return codec

        try:
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'stream=codec_name',
                '-select_streams', 'a:0',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                url
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            
            output = stdout.decode('utf-8', errors='ignore').strip()
            
            if output and output in self.UNSUPPORTED_CODECS:
                self._codec_cache[url_hash] = (output, time.time())
                self._known_transcode_urls.add(url_hash)
                return output
            elif output:
                self._codec_cache[url_hash] = (output, time.time())
                return output

        except asyncio.TimeoutError:
            logger.warning(f"Timeout detectando codec para {url[:50]}...")
        except FileNotFoundError:
            logger.error("ffprobe no encontrado")
        except Exception as e:
            logger.error(f"Error detectando codec: {e}")

        return None

    def needs_transcode(self, codec: Optional[str]) -> bool:
        """Determina si el codec requiere transcodificación"""
        if not codec:
            return False
        return codec.lower() in self.UNSUPPORTED_CODECS

    async def get_stream_with_transcode(
        self,
        original_url: str,
        range_header: Optional[str] = None
    ) -> Optional[Tuple[int, Dict[str, str], AsyncIterator[bytes]]]:
        """
        Obtiene el stream, transcodificando el audio si es necesario.
        
        Args:
            original_url: URL del stream original
            range_header: Header Range para soporte de seek
            
        Returns:
            (status_code, headers, body_iterator) o None si no necesita transcode
        """
        url_hash = self._hash_url(original_url)
        
        # Quick check: si ya sabemos que no necesita transcode, retornar None
        if url_hash not in self._known_transcode_urls:
            # Intentar detectar el codec
            audio_codec = await self.detect_audio_codec(original_url)
            
            if not self.needs_transcode(audio_codec):
                # No necesita transcode
                return None
            
            print(f"Transcode: {audio_codec} -> aac para {original_url[:50]}...")

        # Construir comando FFmpeg para transcodificar solo el audio
        cmd = [
            'ffmpeg',
            '-re',  # Read input at native frame rate
            '-i', original_url,
            '-c:v', 'copy',  # Copiar video sin transcodificar
            '-c:a', 'aac',   # Transcodificar audio a AAC
            '-b:a', '128k',  # Bitrate de audio
            '-f', 'matroska',  # Output formato MKV (soporta AAC)
            '-'
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            async def iter_chunks():
                try:
                    while True:
                        chunk = await proc.stdout.read(8192)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    proc.terminate()
                    try:
                        await proc.wait(timeout=5)
                    except asyncio.TimeoutError:
                        proc.kill()

            headers = {
                'content-type': 'video/x-matroska',
                'accept-ranges': 'none',
                'X-Transcoded': 'true'
            }

            return (200, headers, iter_chunks())

        except Exception as e:
            logger.error(f"Error en transcode: {e}")
            return None

    def clear_cache(self):
        """Limpia el cache de codecs"""
        self._codec_cache.clear()
        self._known_transcode_urls.clear()
