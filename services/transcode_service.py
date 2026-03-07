"""
Servicio de transcodificación para HLS con hls.js y Chromecast

Arquitectura:
- ffmpeg escribe segmentos HLS y playlist .m3u8 a disco (/tmp/hls/{session_id}/)
- La API sirve esos ficheros con los endpoints /hls/{session_id}/playlist.m3u8 y /hls/{session_id}/{segment}
- hls.js los consume directamente
- Limpieza automática de sesiones inactivas cada 2 minutos
"""
import asyncio
import shutil
import os
import time
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

HLS_BASE_DIR = "/tmp/hls"
SEGMENT_DURATION = 4        # segundos por segmento
HLS_LIST_SIZE = 6           # segmentos en ventana deslizante
SESSION_TIMEOUT = 120       # segundos sin actividad para limpiar sesión
PLAYLIST_READY_TIMEOUT = 15 # segundos esperando el primer playlist
FFMPEG_START_MAX_ATTEMPTS = 3
FFMPEG_RETRY_DELAY = 1.0
CHROMECAST_MIN_SEGMENTS = 3


class HlsSession:
    """Representa una sesión de transcodificación HLS activa"""

    def __init__(
        self,
        session_id: str,
        url: str,
        output_dir: str,
        profile: str = "default",
        cache_key: Optional[str] = None
    ):
        self.session_id = session_id
        self.url = url
        self.output_dir = output_dir
        self.profile = profile
        self.cache_key = cache_key or session_id
        self.playlist_path = os.path.join(output_dir, "playlist.m3u8")
        self.process: Optional[asyncio.subprocess.Process] = None
        self.created_at = time.time()
        self.last_accessed = time.time()

    def touch(self):
        self.last_accessed = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_accessed) > SESSION_TIMEOUT

    def playlist_exists(self) -> bool:
        return os.path.exists(self.playlist_path)

    def playlist_segment_count(self) -> int:
        if not self.playlist_exists():
            return 0

        try:
            with open(self.playlist_path, "r", encoding="utf-8") as playlist_file:
                return sum(1 for line in playlist_file if line.strip().endswith((".ts", ".m4s")))
        except OSError:
            return 0

    async def stop(self):
        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        if os.path.exists(self.output_dir):
            shutil.rmtree(self.output_dir, ignore_errors=True)


class TranscodeService:
    """
    Gestiona sesiones HLS activas.
    Cada sesión es un proceso ffmpeg que escribe segmentos a disco.
    """

    # Mantenidos por compatibilidad con código anterior
    _codec_cache: Dict = {}
    _CODEC_CACHE_TTL: float = 3600.0
    UNSUPPORTED_CODECS = {'eac3', 'ac3', 'truehd', 'dts', 'flac'}
    _known_transcode_urls: set = set()

    def __init__(self):
        self._sessions: Dict[str, HlsSession] = {}
        os.makedirs(HLS_BASE_DIR, exist_ok=True)

    async def get_or_create_session(
        self,
        username: str,
        stream_id: str,
        original_url: str,
        profile: str = "default"
    ) -> HlsSession:
        """
        Devuelve sesión activa existente para este usuario+stream, o crea una nueva.
        """
        # Buscar sesión activa reutilizable
        session_cache_key = f"{username}:{stream_id}:{profile}"
        for sid, session in list(self._sessions.items()):
            if session.cache_key == session_cache_key:
                if not session.is_expired() and session.process and session.process.returncode is None:
                    session.touch()
                    return session
                else:
                    await session.stop()
                    del self._sessions[sid]
                    break

        # Crear nueva sesión
        session_id = f"{username}_{stream_id}_{int(time.time())}"
        output_dir = os.path.join(HLS_BASE_DIR, session_id)
        os.makedirs(output_dir, exist_ok=True)

        session = HlsSession(
            session_id=session_id,
            url=original_url,
            output_dir=output_dir,
            profile=profile,
            cache_key=session_cache_key
        )
        self._sessions[session_id] = session

        await self._start_ffmpeg(session)
        return session

    async def _start_ffmpeg(self, session: HlsSession):
        """Arranca ffmpeg para generar los segmentos HLS en disco"""
        is_chromecast_profile = session.profile == "chromecast"
        segment_pattern = os.path.join(
            session.output_dir,
            "segment%d.m4s" if is_chromecast_profile else "segment%d.ts"
        )

        cmd = [
            "ffmpeg",
            "-loglevel", "warning",
            "-fflags", "+genpts",
            "-avoid_negative_ts", "make_zero",
            "-re",
            "-i", session.url
        ]

        if is_chromecast_profile:
            cmd.extend([
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-tune", "zerolatency",
                "-vf", "scale=w=1280:h=720:force_original_aspect_ratio=decrease",
                "-r", "25",
                "-profile:v", "main",
                "-level", "4.1",
                "-pix_fmt", "yuv420p",
                "-b:v", "3000k",
                "-maxrate", "3000k",
                "-bufsize", "6000k",
                "-g", "100",
                "-keyint_min", "100",
                "-sc_threshold", "0",
                "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_DURATION})",
                "-x264-params", "repeat-headers=1:scenecut=0:open-gop=0",
                "-flags", "+cgop",
                "-c:a", "aac",
                "-ar", "48000",
                "-ac", "2",
                "-b:a", "128k"
            ])
        else:
            cmd.extend([
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k"
            ])

        hls_flags = "delete_segments+append_list"
        if is_chromecast_profile:
            hls_flags = "append_list+independent_segments"

        cmd.extend([
            "-f", "hls",
            "-hls_time", str(SEGMENT_DURATION),
            "-hls_list_size", str(HLS_LIST_SIZE),
            "-hls_flags", hls_flags,
            "-hls_segment_filename", segment_pattern,
        ])

        if is_chromecast_profile:
            cmd.extend([
                "-hls_segment_type", "fmp4",
                "-hls_playlist_type", "event",
                "-start_number", "1",
                "-hls_fmp4_init_filename", "init.mp4",
            ])
        else:
            cmd.extend([
                "-hls_segment_type", "mpegts",
            ])

        cmd.append(session.playlist_path)

        try:
            session.process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            logger.info(
                f"🎬 HLS session started: {session.session_id}, "
                f"profile={session.profile}, pid={session.process.pid}"
            )
            asyncio.create_task(self._log_ffmpeg_stderr(session))
        except Exception as e:
            logger.error(f"❌ Error arrancando ffmpeg: {e}")

    @staticmethod
    def _clean_session_output(session: HlsSession):
        """Elimina artefactos parciales antes de reintentar ffmpeg."""
        if not os.path.exists(session.output_dir):
            return
        for filename in os.listdir(session.output_dir):
            if filename == "playlist.m3u8" or filename.startswith("segment"):
                file_path = os.path.join(session.output_dir, filename)
                if os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass

    async def _log_ffmpeg_stderr(self, session: HlsSession):
        if not session.process or not session.process.stderr:
            return
        try:
            async for line in session.process.stderr:
                text = line.decode('utf-8', errors='ignore').strip()
                if text:
                    logger.debug(f"[ffmpeg:{session.session_id}] {text}")
        except Exception:
            pass

    async def wait_for_playlist(self, session: HlsSession) -> bool:
        """Espera hasta que ffmpeg genere el primer playlist.m3u8"""
        deadline = time.time() + PLAYLIST_READY_TIMEOUT
        attempts = 1

        while time.time() < deadline:
            if session.playlist_exists():
                if session.profile != "chromecast":
                    return True

                if session.playlist_segment_count() >= CHROMECAST_MIN_SEGMENTS:
                    return True

            if not session.process:
                logger.warning(f"ffmpeg no pudo iniciarse para sesión HLS: {session.session_id}")
                return False

            if session.process and session.process.returncode is not None:
                if attempts >= FFMPEG_START_MAX_ATTEMPTS:
                    logger.warning(
                        f"ffmpeg terminó antes de generar playlist: {session.session_id} "
                        f"(reintentos agotados: {attempts}/{FFMPEG_START_MAX_ATTEMPTS})"
                    )
                    return False

                attempts += 1
                delay = FFMPEG_RETRY_DELAY * (attempts - 1)
                logger.warning(
                    f"ffmpeg terminó antes de generar playlist: {session.session_id}. "
                    f"Reintentando ({attempts}/{FFMPEG_START_MAX_ATTEMPTS}) en {delay:.1f}s"
                )
                self._clean_session_output(session)
                await asyncio.sleep(delay)
                await self._start_ffmpeg(session)
                continue

            await asyncio.sleep(0.3)
        return False

    def get_session(self, session_id: str) -> Optional[HlsSession]:
        session = self._sessions.get(session_id)
        if session:
            session.touch()
        return session

    def get_file_path(self, session_id: str, filename: str) -> Optional[str]:
        """Devuelve ruta de un fichero HLS si existe"""
        session = self.get_session(session_id)
        if not session:
            return None
        path = os.path.join(session.output_dir, filename)
        return path if os.path.exists(path) else None

    async def cleanup_expired(self):
        """Limpia sesiones expiradas"""
        expired = [sid for sid, s in self._sessions.items() if s.is_expired()]
        for sid in expired:
            session = self._sessions.pop(sid)
            await session.stop()
            logger.info(f"🧹 HLS session cleaned: {sid}")

    async def stop_all(self):
        for session in list(self._sessions.values()):
            await session.stop()
        self._sessions.clear()

    # ── Compatibilidad con código anterior ──────────────────────────

    def needs_transcode(self, codec: Optional[str]) -> bool:
        return True

    def clear_cache(self):
        self._codec_cache.clear()
        self._known_transcode_urls.clear()
