"""
IPTV API — Modular routers

Arquitectura:
- scripts/api.py: setup + include_router() + lifespan (~100 lines)
- app/routers/*.py: endpoints agrupados por dominio
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from iptv_api.core.config import get_settings
from iptv_api.core.dependencies import get_transcode_service
from iptv_api.routers import (
    admin,
    auth,
    calendar,
    channel_favorites,
    content,
    health,
    logo,
    playback_preferences,
    replays,
    series,
    streams,
    video_extractor,
    watch_progress,
)
from iptv_api.services.transcode_service import TranscodeService

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)

logger = logging.getLogger("iptv-api")

settings = get_settings()

# ============================================
# Ciclo de Vida
# ============================================


async def cleanup_sessions_task():
    from iptv_api.database import get_session
    from iptv_api.repositories.session_repo import SessionRepository

    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_minutes * 60)

            def _do_cleanup():
                session = get_session()
                try:
                    repo = SessionRepository(session)
                    cleaned = repo.cleanup_inactive(settings.session_timeout_minutes)
                    session.commit()
                    return cleaned
                finally:
                    session.close()

            cleaned = await asyncio.to_thread(_do_cleanup)
            if cleaned > 0:
                print(f"🧹 Limpiadas {cleaned} sesiones inactivas")
        except Exception as e:
            print(f"❌ Error en limpieza de sesiones: {e}")


async def cleanup_hls_task():
    """Tarea periódica para limpiar sesiones HLS expiradas (cada 2 min)"""
    while True:
        try:
            await asyncio.sleep(120)
            transcode_svc = get_transcode_service()
            await transcode_svc.cleanup_expired()
        except Exception as e:
            print(f"❌ Error en limpieza HLS: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gestión del ciclo de vida de la aplicación"""
    print("🚀 Iniciando IPTV API...")

    if not settings.is_valid():
        print("❌ Error: Configuración incompleta")
    else:
        from iptv_api.database import get_session
        from iptv_api.repositories.channel_repo import ChannelRepository
        from iptv_api.repositories.config_repo import ConfigRepository
        from iptv_api.repositories.content_repo import ContentRepository
        from iptv_api.repositories.series_repo import SeriesRepository
        from iptv_api.services.stream_service import StreamProxyServiceV2

        session = get_session()
        try:
            stream_svc = StreamProxyServiceV2(
                config_repo=ConfigRepository(session),
                channel_repo=ChannelRepository(session),
                content_repo=ContentRepository(session),
                series_repo=SeriesRepository(session),
            )
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(stream_svc.preload_cache),
                    timeout=15.0,
                )
            except Exception as e:
                print(f"⚠️ Warning: preload_cache failed ({e}), continuing without cache")
        finally:
            session.close()
        import iptv_api.core.dependencies as deps

        deps.transcode_service = TranscodeService()
        app.state.background_tasks = [
            asyncio.create_task(cleanup_sessions_task()),
            asyncio.create_task(cleanup_hls_task()),
        ]
        print("✅ IPTV API iniciada correctamente")

    yield

    print("🛑 Cerrando IPTV API...")
    for task in getattr(app.state, "background_tasks", []):
        task.cancel()
    try:
        transcode_svc = get_transcode_service()
        await transcode_svc.stop_all()
    except Exception as exc:
        logger.warning("No se pudo detener el servicio de transcodificación: %s", exc)
    try:
        from iptv_api.services.stream_service import StreamProxyServiceV2

        await StreamProxyServiceV2.close_all_clients()
    except Exception as exc:
        logger.warning("No se pudieron cerrar los clientes HTTP de streams: %s", exc)


# ============================================
# Crear aplicación
# ============================================

app = FastAPI(
    title="IPTV API",
    description="API para gestión de usuarios IPTV con control de dispositivos y JWT",
    version="2.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://walactvweb.walerike.com", "http://localhost:4200"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

# Servir imágenes placeholder estáticas
IMAGES_DIR = Path(__file__).parent.parent / "resources" / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets/images", StaticFiles(directory=str(IMAGES_DIR)), name="images")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Manejador global que asegura cabeceras CORS en errores"""
    return JSONResponse(
        status_code=500, content={"error": "Internal Server Error", "message": str(exc)}
    )


# ============================================
# Registrar routers modulares
# ============================================

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(content.router)
app.include_router(channel_favorites.router)
app.include_router(series.router)
app.include_router(watch_progress.router)
app.include_router(playback_preferences.router)
app.include_router(calendar.router)
app.include_router(replays.router)
app.include_router(streams.router)
app.include_router(video_extractor.router)
app.include_router(logo.router)


# ============================================
# Main
# ============================================

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=3010, reload=True)
