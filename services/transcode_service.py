"""
Servicio de transcodificación para HLS con hls.js
Convierte streams a HLS para compatibilidad con hls.js en navegadores
"""
import asyncio
import subprocess
from typing import Optional, Tuple, AsyncIterator, Dict, Any
import hashlib
import time
import logging
import os

logger = logging.getLogger(__name__)


class TranscodeService:
    """
    Servicio para transcodificar streams a HLS para compatibilidad con hls.js.
    """

    _codec_cache: Dict[str, Tuple[str, float]] = {}
    _CODEC_CACHE_TTL: float = 3600.0

    UNSUPPORTED_CODECS = {'eac3', 'ac3', 'truehd', 'dts', 'flac'}
    _known_transcode_urls: set = set()

    def __init__(self):
        pass

    def _hash_url(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()[:16]

    async def detect_audio_codec(self, url: str) -> Optional[str]:
        url_hash = self._hash_url(url)
        cached = self._codec_cache.get(url_hash)
        
        if cached:
            codec, cached_at = cached
            if (time.time() - cached_at) < self._CODEC_CACHE_TTL:
                return codec

        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-show_entries', 'stream=codec_name',
                '-select_streams', 'a:0',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                url
            ]

            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
            output = stdout.decode('utf-8', errors='ignore').strip()
            
            if output:
                if output in self.UNSUPPORTED_CODECS:
                    self._codec_cache[url_hash] = (output, time.time())
                    self._known_transcode_urls.add(url_hash)
                else:
                    self._codec_cache[url_hash] = (output, time.time())
                return output

        except Exception as e:
            logger.error(f"Error detectando codec: {e}")

        return None

    def needs_transcode(self, codec: Optional[str]) -> bool:
        if not codec:
            return False
        return codec.lower() in self.UNSUPPORTED_CODECS

    async def stream_hls(
        self,
        original_url: str
    ) -> AsyncIterator[bytes]:
        """
        Genera un stream HLS transcodificando a AAC si es necesario.
        Yield chunks del playlist HLS y segmentos.
        """
        url_hash = self._hash_url(original_url)
        
        audio_codec = None
        if url_hash not in self._known_transcode_urls:
            audio_codec = await self.detect_audio_codec(original_url)
            if audio_codec:
                if audio_codec in self.UNSUPPORTED_CODECS:
                    self._known_transcode_urls.add(url_hash)

        needs_audio_transcode = self.needs_transcode(audio_codec)
        audio_codec_arg = 'aac' if needs_audio_transcode else 'copy'
        
        cmd = [
            'ffmpeg', '-re', '-i', original_url,
            '-c:v', 'copy',
            '-c:a', audio_codec_arg,
        ]
        
        if needs_audio_transcode:
            cmd.extend(['-b:a', '128k'])
        
        cmd.extend([
            '-f', 'hls',
            '-hls_time', '4',
            '-hls_list_size', '4',
            '-hls_flags', 'delete_segments',
            '-hls_segment_filename', '/tmp/hls/%s-segment%d.ts' % (url_hash, int(time.time())),
            '-start_number', '1',
            '-'
        ])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        try:
            while True:
                chunk = await proc.stdout.read(8192)
                if not chunk:
                    break
                yield chunk
        finally:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()

    def clear_cache(self):
        self._codec_cache.clear()
        self._known_transcode_urls.clear()
