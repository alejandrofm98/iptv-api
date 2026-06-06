"""
IPTV API - Refactorizada con mejores prácticas REST

Cambios principales:
- Paginación estándar (page/page_size) en lugar de skip/limit
- Dependencias reutilizables para autenticación
- Endpoints unificados para contenido
- Manejo de errores consistente
- Estructura organizada con prefijos claros
"""

import asyncio
import gzip
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    StreamingResponse,
)
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel
from starlette.responses import RedirectResponse

from app.services.calendar_service import CalendarServiceV2
from app.services.channel_favorites_service import ChannelFavoritesServiceV2
from app.services.content_service import ContentServiceV2
from app.services.device_service import DeviceServiceV2
from app.services.playlist_service import PlaylistServiceV2
from app.services.stream_service import StreamProxyServiceV2
from app.services.user_service import UserServiceV2
from app.services.watch_progress_service import WatchProgressServiceV2
from services.transcode_service import TranscodeService
from services.video_extractor_service import VideoExtractorService
from utils.config import get_settings
from utils.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM
from utils.dependencies import AuthResult as AuthDep
from utils.dependencies import (
    get_calendar_service_v2,
    get_channel_favorites_service_v2,
    get_content_service_v2,
    get_device_service_v2,
    get_playlist_service_v2,
    get_stream_service_v2,
    get_transcode_service,
    get_user_service_v2,
    get_watch_progress_service_v2,
    require_admin,
    require_auth_with_jwt,
)
from utils.exceptions import (
    BadRequestException,
    ForbiddenException,
    NotFoundException,
    TooManyRequestsException,
    UnauthorizedException,
)
from utils.models import (
    CalendarDayResponse,
    CalendarEvent,
    ChannelFavoriteCreate,
    SystemStats,
    Token,
    UserCreate,
    UserUpdate,
    WatchProgressUpsert,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)

logger = logging.getLogger("iptv-api")

# Configuración
settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = JWT_ACCESS_TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

ALLOWED_WEB_ORIGINS = ["https://walactvweb.walerike.com", "http://localhost:4200"]


# ============================================
# Ciclo de Vida
# ============================================


async def cleanup_sessions_task():
    from app.database import SessionLocal
    from app.repositories.session_repo import SessionRepository

    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_minutes * 60)

            def _do_cleanup():
                session = SessionLocal()
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
        from app.database import SessionLocal
        from app.repositories.channel_repo import ChannelRepository
        from app.repositories.config_repo import ConfigRepository
        from app.repositories.content_repo import ContentRepository
        from app.repositories.series_repo import SeriesRepository
        from app.services.stream_service import StreamProxyServiceV2

        session = SessionLocal()
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
        import utils.dependencies as deps

        deps.transcode_service = TranscodeService()
        background_tasks = []
        background_tasks.append(asyncio.create_task(cleanup_sessions_task()))
        background_tasks.append(asyncio.create_task(cleanup_hls_task()))
        print("✅ IPTV API iniciada correctamente")

    yield

    print("🛑 Cerrando IPTV API...")
    try:
        transcode_svc = get_transcode_service()
        await transcode_svc.stop_all()
    except Exception:
        pass


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
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=500, content={"error": "Internal Server Error", "message": str(exc)}
    )


def get_video_extractor() -> VideoExtractorService:
    """Dependencia reutilizable — instancia sin estado, segura para concurrencia."""
    return VideoExtractorService()


# ─── modelos Pydantic ────────────────────────────────────────────────────────
class ExtractRequest(BaseModel):
    url: str
    """URL de embed del proveedor (streamtape, netu, streamwish, etc.)"""


class ExtractMultiRequest(BaseModel):
    urls: list[str]
    """Lista de URLs a extraer en paralelo (máximo 10)."""


# ============================================
# Funciones de Utilidad
# ============================================


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Crea un token JWT de acceso"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def validate_stream_token(token: str) -> dict:
    """Valida un JWT para uso en proxy de streaming."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")

        if user_id is None or role is None:
            raise UnauthorizedException("Token inválido")

        return {"id": user_id, "role": role}
    except JWTError:
        raise UnauthorizedException("Token inválido o expirado") from None


def build_replay_proxy_url(target_url: str, token: str) -> str:
    encoded_url = quote(target_url, safe="")
    encoded_token = quote(token, safe="")
    return f"/api/replay-proxy?url={encoded_url}&token={encoded_token}"


def rewrite_m3u8_content(content: str, source_url: str, token: str) -> str:
    rewritten_lines = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            rewritten_lines.append(raw_line)
            continue

        absolute_url = urljoin(source_url, line)
        rewritten_lines.append(build_replay_proxy_url(absolute_url, token))

    return "\n".join(rewritten_lines)


def build_replay_upstream_headers(url: str, request: Request) -> dict:
    lowered_url = url.lower()
    headers = {
        "User-Agent": "PostmanRuntime/7.43.0",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    request_range = request.headers.get("range")
    if request_range:
        headers["Range"] = request_range

    if (
        "dailymotion.com" in lowered_url
        or "dmcdn.net" in lowered_url
        or "dmxleo.dailymotion.com" in lowered_url
    ):
        headers.update(
            {
                "Postman-Token": "iptv-api-replay-proxy",
                "Cookie": "dmvk=69adf139dae1d; ts=966072; v1st=4068e8cf-d19d-4adc-a624-69ab5f4c5a48",
            }
        )

    return headers


# ============================================
# Health Check
# ============================================


@app.get("/", tags=["Health"])
async def root():
    return {"service": "IPTV API", "version": "2.1.0", "status": "running"}


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}


# ============================================
# API: Autenticación
# ============================================


@app.post("/api/auth/login", response_model=Token, tags=["Auth"])
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Endpoint de Login. Retorna JWT token."""
    user = svc.get_by_username(form_data.username)

    if not user:
        raise UnauthorizedException("Usuario o contraseña incorrectos")

    if not svc._verify_password(form_data.password, user["password_hash"]):
        raise UnauthorizedException("Usuario o contraseña incorrectos")

    if not user.get("is_active", True):
        raise ForbiddenException("Usuario inactivo")

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user["id"], "role": user.get("role", "user")},
        expires_delta=access_token_expires,
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.get("role", "user"),
    }


# ============================================
# API: Usuarios (Admin)
# ============================================


@app.post("/api/admin/users", response_model=dict, tags=["Admin - Users"])
async def create_user(
    user_data: UserCreate,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Crear nuevo usuario (Solo Admin)"""
    try:
        return svc.create_user_from_model(user_data)
    except ValueError as e:
        raise BadRequestException(str(e)) from e


@app.get("/api/admin/users", response_model=dict, tags=["Admin - Users"])
async def list_users(
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(100, ge=1, le=1000, description="Items por página"),
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Listar usuarios paginados (Solo Admin)"""
    items, total = svc.list_users(page, page_size)
    pages = (total + page_size - 1) // page_size

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_next": page < pages,
        "has_prev": page > 1,
    }


@app.get("/api/admin/users/{user_id}", response_model=dict, tags=["Admin - Users"])
async def get_user(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Obtener usuario por ID (Solo Admin)"""
    user = svc.get_user(user_id)
    if not user:
        raise NotFoundException("Usuario", user_id)
    return user


@app.put("/api/admin/users/{user_id}", response_model=dict, tags=["Admin - Users"])
async def update_user(
    user_id: str,
    user_data: UserUpdate,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Actualizar usuario (Solo Admin)"""
    user = svc.update_user(user_id, user_data)
    if not user:
        raise NotFoundException("Usuario", user_id)
    return user


@app.delete("/api/admin/users/{user_id}", tags=["Admin - Users"])
async def delete_user(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: UserServiceV2 = Depends(get_user_service_v2),
):
    """Eliminar usuario (Solo Admin)"""
    success = svc.delete_user(user_id)
    if not success:
        raise NotFoundException("Usuario", user_id)
    return {"message": "Usuario eliminado"}


# ============================================
# API: Dispositivos (Admin)
# ============================================


@app.get("/api/admin/users/{user_id}/devices", response_model=list, tags=["Admin - Devices"])
async def get_user_devices(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Obtiene dispositivos de un usuario"""
    return svc.get_user_devices(user_id)


@app.delete("/api/admin/users/{user_id}/devices/{device_id}", tags=["Admin - Devices"])
async def disconnect_device(
    user_id: str,
    device_id: str,
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Desconecta un dispositivo específico"""
    success = svc.disconnect_device(user_id, device_id)
    if not success:
        raise NotFoundException("Dispositivo", device_id)
    return {"message": "Dispositivo desconectado"}


@app.delete("/api/admin/users/{user_id}/devices", tags=["Admin - Devices"])
async def disconnect_all_devices(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Desconecta todos los dispositivos de un usuario"""
    count = svc.disconnect_all_devices(user_id)
    return {"message": f"{count} dispositivos desconectados"}


@app.get("/api/admin/sessions", tags=["Admin - Devices"])
async def get_all_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    _: dict = Depends(require_admin),
    svc: DeviceServiceV2 = Depends(get_device_service_v2),
):
    """Obtiene todas las sesiones activas paginadas"""
    sessions = svc.get_all_sessions(page_size * page)
    total = len(sessions)
    start = (page - 1) * page_size
    end = start + page_size
    pages = (total + page_size - 1) // page_size

    return {
        "items": sessions[start:end],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


# ============================================
# API: Stats (Admin)
# ============================================


@app.get("/api/admin/stats", response_model=SystemStats, tags=["Admin - Stats"])
async def get_system_stats(
    _: dict = Depends(require_admin),
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    playlist_svc: PlaylistServiceV2 = Depends(get_playlist_service_v2),
):
    """Obtiene estadísticas del sistema"""
    _, total_users = user_svc.list_users(page=1, page_size=1)
    sessions = device_svc.get_all_sessions(limit=10000)
    playlist_stats = playlist_svc.get_playlist_stats()

    return SystemStats(
        total_users=total_users,
        active_users=total_users,
        total_sessions=len(sessions),
        total_channels=playlist_stats["total_channels"],
        total_movies=playlist_stats["total_movies"],
        total_series=playlist_stats["total_series"],
    )


@app.get("/api/admin/resilience", tags=["Admin - Stats"])
async def get_resilience_status(
    _: dict = Depends(require_admin),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
):
    """Obtiene el estado de resiliencia de streams (circuit breaker, retry, buffer)"""
    return stream_svc.get_resilience_status()


# ============================================
# API: Contenido (Público)
# ============================================


@app.get("/api/content/groups", tags=["Content"])
async def get_groups_public(
    content_type: str = Query("channels", enum=["channels", "movies", "series"]),
    countries: str | None = Query(
        None, description="Filtrar por países (separados por coma: US,MX,ES)"
    ),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    country_list = None
    if countries:
        country_list = [c.strip().upper() for c in countries.split(",") if c.strip()]

    groups = content_svc.get_groups(content_type, country_list)
    if content_type == "channels" and "Favorites" not in groups:
        groups = ["Favorites", *groups]
    return {"groups": groups}


@app.get("/api/content/countries", tags=["Content"])
async def get_countries_public(
    content_type: str = Query("channels", enum=["channels", "movies", "series"]),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return {"countries": content_svc.get_countries(content_type)}


@app.post("/api/admin/content/reload", tags=["Admin - Content"])
async def reload_template(
    _: dict = Depends(require_admin),
    playlist_svc: PlaylistServiceV2 = Depends(get_playlist_service_v2),
):
    """Recarga los templates M3U en memoria"""
    playlist_svc.reload_template()
    templates = playlist_svc._templates
    if templates.get("full"):
        return {
            "status": "success",
            "message": "Templates recargados correctamente",
            "templates": {k: len(v) if v else 0 for k, v in templates.items()},
        }
    else:
        raise BadRequestException(
            "No se pudo recargar el template. Verifica que playlist_template.m3u exista."
        )


# ============================================
# API: Contenido (Público - Requiere Auth)
# ============================================


@app.get("/api/content", tags=["Content"])
async def get_content(
    content_type: str = Query(
        ..., enum=["channels", "movies", "series"], description="Tipo de contenido"
    ),
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(50, ge=1, le=100, description="Items por página"),
    group: str | None = Query(None, description="Filtrar por grupo"),
    country: str | None = Query(None, description="Filtrar por país"),
    search: str | None = Query(None, description="Buscar por nombre"),
    year: int | None = Query(None, description="Filtrar por año"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    section_title: str | None = Query(
        None,
        description="Título de sección del home para paginación consistente (ej: 2026 ESTRENOS, NETFLIX)",
    ),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    if content_type == "channels" and group == "Favorites":
        return favorites_svc.get_favorite_channels(
            user_id=auth.user_id,
            content_svc=content_svc,
            page=page,
            page_size=page_size,
            country=country,
            search=search,
            username=auth.username,
            password=password or "",
        )

    if section_title and content_type in ("movies", "series"):
        result = content_svc.get_section_page(
            content_type=content_type,
            section_title=section_title,
            page=page,
            page_size=page_size,
            username=auth.username,
            password=password or "",
            country=country,
        )
        if result is None:
            raise NotFoundException("Sección", section_title)
        return result

    return content_svc.get_android_content_list(
        content_type=content_type,
        page=page,
        page_size=page_size,
        group=group,
        country=country,
        search=search,
        year=year,
        username=auth.username,
        password=password or "",
    )


@app.get("/api/content/filters", tags=["Content"])
async def get_content_filters(
    content_type: str = Query(
        ..., enum=["channels", "movies", "series"], description="Tipo de contenido"
    ),
    country: str | None = Query(None, description="Filtrar grupos por país"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    payload = content_svc.get_catalog_filters(content_type=content_type, country=country)
    if content_type == "channels" and "Favorites" not in payload["groups"]:
        payload = {**payload, "groups": ["Favorites", *payload["groups"]]}
    return payload


@app.get("/api/content/stats", tags=["Content"])
async def get_content_stats(
    content_type: str = Query(
        ..., enum=["channels", "movies", "series"], description="Tipo de contenido"
    ),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return content_svc.get_content_stats(content_type=content_type)


# ============================================
# IMPORTANTE: rutas /full y /all ANTES de /{content_type}/{item_id}
# para evitar que el endpoint genérico las capture primero.
# ============================================


@app.get("/api/full/channels", response_class=JSONResponse, tags=["Content"])
async def get_channels_full(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    import os

    json_data = content_svc.get_all_content_bulk("channels")
    for base_dir in [
        "/app/data/json",
        os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "walactv-scrapper",
            "data",
            "json",
        ),
    ]:
        gz_path = os.path.join(base_dir, "channels.json.gz")
        if os.path.exists(gz_path):
            with open(gz_path, "rb") as f:
                gz_data = f.read()
            return Response(
                content=gz_data,
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(gz_data)),
                    "X-Content-Type": "channels.json",
                },
            )
    return json_data


@app.get("/api/full/movies", response_class=JSONResponse, tags=["Content"])
async def get_movies_full(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    import os

    json_data = content_svc.get_all_content_bulk("movies")
    for base_dir in [
        "/app/data/json",
        os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "walactv-scrapper",
            "data",
            "json",
        ),
    ]:
        gz_path = os.path.join(base_dir, "movies.json.gz")
        if os.path.exists(gz_path):
            with open(gz_path, "rb") as f:
                gz_data = f.read()
            return Response(
                content=gz_data,
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(gz_data)),
                    "X-Content-Type": "movies.json",
                },
            )
    return json_data


@app.get("/api/full/series", response_class=JSONResponse, tags=["Content"])
async def get_series_full(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    import os

    json_data = content_svc.get_all_content_bulk("series")
    for base_dir in [
        "/app/data/json",
        os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "walactv-scrapper",
            "data",
            "json",
        ),
    ]:
        gz_path = os.path.join(base_dir, "series.json.gz")
        if os.path.exists(gz_path):
            with open(gz_path, "rb") as f:
                gz_data = f.read()
            return Response(
                content=gz_data,
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(gz_data)),
                    "X-Content-Type": "series.json",
                },
            )
    return json_data


@app.get("/api/full/channels/legacy", tags=["Content"])
async def get_all_channels_bulk(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return content_svc.get_all_channels_bulk()


# ============================================
# IMPORTANTE: este endpoint genérico va DESPUÉS de todos los
# endpoints con segmentos literales bajo /api/content/{x}/{y}
# ============================================

# Valores reservados que tienen su propio endpoint — nunca deben llegar aquí
_RESERVED_ITEM_IDS = {"full", "all"}


@app.get("/api/content/{content_type}/{item_id}", tags=["Content"])
async def get_content_item(
    content_type: str,
    item_id: str,
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    if content_type not in ["channels", "movies", "series"]:
        raise BadRequestException("Tipo de contenido inválido")

    if item_id in _RESERVED_ITEM_IDS:
        raise BadRequestException(
            f"'{item_id}' no es un ID válido. "
            f"Para obtener todo el contenido usa /api/content/{content_type}/{item_id}"
        )

    item = content_svc.get_content_item(
        content_type=content_type,
        item_id=item_id,
        username=auth.username,
        password=password or "",
    )

    if not item:
        content_name = {"channels": "Canal", "movies": "Película", "series": "Serie"}[content_type]
        raise NotFoundException(content_name, item_id)

    return item


@app.get("/api/home", tags=["Content"])
async def get_home(
    page_size: int = Query(24, ge=1, le=50, description="Items por bloque"),
    country: str | None = Query(None, description="Filtrar home por country, por ejemplo ES o EN"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    """Obtiene bloques ligeros para la home de clientes TV."""
    payload = content_svc.get_home_catalog_new(
        username=auth.username, country=country, password=password or ""
    )
    payload["favorites"] = favorites_svc.get_favorite_channels(
        user_id=auth.user_id,
        content_svc=content_svc,
        page=1,
        page_size=page_size,
        country=None,
        search=None,
        username=auth.username,
        password=password or "",
    )["items"]
    logger.info(
        "/api/home: user=%s country=%s favorites=%d",
        auth.username,
        country,
        len(payload["favorites"]),
    )
    return payload


@app.get("/api/home2", tags=["Content"])
async def get_home_v2(
    page_size: int = Query(24, ge=1, le=50, description="Items por bloque"),
    country: str | None = Query(None, description="Filtrar home por country"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    """Obtiene bloques ligeros para la home (nueva versión con paginación infinita)."""
    return content_svc.get_home_catalog_new(
        username=auth.username,
        country=country,
        password=password or "",
    )


@app.get("/api/content/section", tags=["Content"])
async def get_section(
    content_type: str = Query(..., enum=["movies", "series"], description="Tipo de contenido"),
    section_title: str = Query(..., description="Título de sección: NETFLIX, 2026 ESTRENOS, ..."),
    page: int = Query(
        2,
        ge=1,
        description="Página a cargar (1 = lo que ya tiene /home → empezar en 2)",
    ),
    page_size: int = Query(24, ge=1, le=50, description="Items por página"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    """Carga más items de una sección específica. Para paginación infinita."""
    result = content_svc.get_section_page(
        content_type=content_type,
        section_title=section_title,
        page=page,
        page_size=page_size,
        username=auth.username,
        password=password or "",
        country=None,
    )
    if result is None:
        raise NotFoundException("Sección", section_title)
    return result


@app.get("/api/channel-favorites", tags=["Channel Favorites"])
async def list_channel_favorites(
    auth: AuthDep = Depends(require_auth_with_jwt),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    items = favorites_svc.list_favorites(auth.user_id)
    return {"items": items, "total": len(items)}


@app.post("/api/channel-favorites", tags=["Channel Favorites"])
async def add_channel_favorite(
    body: ChannelFavoriteCreate,
    auth: AuthDep = Depends(require_auth_with_jwt),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    return favorites_svc.add_favorite(auth.user_id, body.channel_provider_id)


@app.delete("/api/channel-favorites/{channel_provider_id}", tags=["Channel Favorites"])
async def delete_channel_favorite(
    channel_provider_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    deleted = favorites_svc.remove_favorite(auth.user_id, channel_provider_id)
    if not deleted:
        raise NotFoundException("ChannelFavorite", channel_provider_id)
    return {"deleted": True, "channel_provider_id": channel_provider_id}


@app.get("/api/search", tags=["Content"])
async def search_content(
    q: str = Query(..., min_length=1, description="Texto de búsqueda"),
    types: str | None = Query(None, description="Tipos separados por coma: channels,movies,series"),
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(50, ge=1, le=100, description="Items por página"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    """Busca contenido en varios tipos sin descargar la playlist completa."""
    requested_types = [
        value.strip() for value in (types or "channels,movies,series").split(",") if value.strip()
    ]
    return content_svc.search_catalog(
        query=q,
        types=requested_types,
        page=page,
        page_size=page_size,
        username=auth.username,
        password=password or "",
    )


@app.get("/api/replays", tags=["Replays"])
async def get_replays(
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(24, ge=1, le=100, description="Items por página"),
    event_type: str | None = Query(None, description="Filtrar por tipo de evento"),
    search: str | None = Query(None, description="Buscar por título o descripción"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return content_svc.get_replays(
        page=page,
        page_size=page_size,
        event_type=event_type,
        search=search,
    )


@app.get("/api/replays/{slug}", tags=["Replays"])
async def get_replay(
    slug: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    item = content_svc.get_replay(slug)
    if not item:
        raise NotFoundException("Replay", slug)
    return item


@app.get("/api/replay-proxy", tags=["Replays"])
async def proxy_replay_stream(
    request: Request,
    url: str = Query(..., description="URL remota del manifiesto o segmento"),
    token: str = Query(..., description="JWT para autorizar el proxy"),
):
    """Proxy de manifests HLS y ficheros directos para evitar problemas CORS en frontend."""
    validate_stream_token(token)

    lowered_url = url.lower()
    upstream_headers = build_replay_upstream_headers(url, request)

    try:
        if lowered_url.endswith(".m3u8"):
            upstream = await asyncio.to_thread(
                lambda: requests.get(url, timeout=30, headers=upstream_headers)
            )
            upstream.raise_for_status()
            rewritten = rewrite_m3u8_content(upstream.text, url, token)
            return Response(
                content=rewritten,
                media_type="application/vnd.apple.mpegurl",
                headers={
                    "Cache-Control": "no-store",
                },
            )

        upstream = await asyncio.to_thread(
            lambda: requests.get(url, stream=True, timeout=60, headers=upstream_headers)
        )
        upstream.raise_for_status()
        media_type = upstream.headers.get("content-type", "application/octet-stream").split(";")[0]
        response_headers = {
            "Cache-Control": "no-store",
        }
        content_length = upstream.headers.get("content-length")
        if content_length:
            response_headers["Content-Length"] = content_length
        content_range = upstream.headers.get("content-range")
        if content_range:
            response_headers["Content-Range"] = content_range
        accept_ranges = upstream.headers.get("accept-ranges")
        if accept_ranges:
            response_headers["Accept-Ranges"] = accept_ranges

        def stream_iterator():
            with upstream:
                for chunk in upstream.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        yield chunk

        return StreamingResponse(
            stream_iterator(),
            media_type=media_type,
            status_code=upstream.status_code,
            headers=response_headers,
        )
    except requests.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Error remoto proxy replay: {e}") from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error proxy replay: {e}") from e


@app.get("/api/replays/{slug}/stream/{source_index}/{button_index}", tags=["Replays"])
async def proxy_replay_source_stream(
    slug: str,
    source_index: int,
    button_index: int,
    request: Request,
    token: str = Query(..., description="JWT para autorizar el proxy"),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    """Resuelve una URL fresca para una fuente de replay y la proxya."""
    validate_stream_token(token)

    resolved = content_svc.resolve_replay_source_stream_url(slug, source_index, button_index)
    if not resolved or not resolved.get("stream_url"):
        raise NotFoundException("Replay source", f"{slug}:{source_index}:{button_index}")

    return await proxy_replay_stream(
        request=request,
        url=resolved["stream_url"],
        token=token,
    )


# ============================================
# API: Calendar
# ============================================


@app.get("/api/calendar/{fecha}", response_model=CalendarDayResponse, tags=["Calendar"])
async def get_calendar_by_date(
    fecha: str,
    password: str | None = Query(None, description="Password para construir stream_url"),
    client: str | None = Query(None, description="'android' para URLs con /live/"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    calendar_svc: CalendarServiceV2 = Depends(get_calendar_service_v2),
):
    from datetime import datetime

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise BadRequestException("Formato de fecha inválido. Use YYYY-MM-DD") from None

    eventos_raw = calendar_svc.get_events_by_date(fecha)

    if not eventos_raw:
        return CalendarDayResponse(fecha=fecha, total_eventos=0, eventos=[])

    username = auth.username or ""
    pwd = password or ""
    base_url = settings.public_domain.rstrip("/")

    all_channel_ids = []
    for evento in eventos_raw:
        for ch in evento.get("canales_resueltos", []) or []:
            cid = ch.get("channel_id")
            if cid:
                all_channel_ids.append(cid)

    provider_map = calendar_svc.get_provider_ids(all_channel_ids) if all_channel_ids else {}

    eventos = []
    for evento in eventos_raw:
        canales_resueltos = evento.get("canales_resueltos", []) or []
        if username and pwd:
            for ch in canales_resueltos:
                stream_id = ch.get("provider_id") or provider_map.get(ch.get("channel_id"))
                if stream_id:
                    ch["provider_id"] = stream_id
                    if client == "android":
                        ch["stream_url"] = f"{base_url}/live/{username}/{pwd}/{stream_id}"
                    elif not ch.get("stream_url"):
                        ch["stream_url"] = f"{base_url}/{username}/{pwd}/{stream_id}"
        eventos.append(
            CalendarEvent(
                id=str(evento["id"]),
                fecha=evento.get("fecha"),
                hora=evento.get("hora"),
                competicion=evento.get("competicion"),
                subtitulo_competicion=evento.get("subtitulo_competicion"),
                categoria=evento.get("categoria"),
                equipos=evento.get("equipos"),
                imagen_evento=evento.get("imagen_evento"),
                canales_original=evento.get("canales_original", []) or [],
                canales_resueltos=canales_resueltos,
            )
        )

    return CalendarDayResponse(fecha=fecha, total_eventos=len(eventos), eventos=eventos)


@app.get("/api/calendar/event/{event_id}", response_model=CalendarEvent, tags=["Calendar"])
async def get_calendar_event(
    event_id: str,
    password: str | None = Query(None, description="Password para construir stream_url"),
    client: str | None = Query(None, description="'android' para URLs con /live/"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    calendar_svc: CalendarServiceV2 = Depends(get_calendar_service_v2),
):
    evento = calendar_svc.get_event_by_id(event_id)

    if not evento:
        raise NotFoundException("Evento", event_id)

    canales_resueltos = evento.get("canales_resueltos", []) or []

    username = auth.username or ""
    pwd = password or ""
    base_url = settings.public_domain.rstrip("/")

    if username and pwd:
        all_channel_ids = [ch.get("channel_id") for ch in canales_resueltos if ch.get("channel_id")]
        provider_map = calendar_svc.get_provider_ids(all_channel_ids) if all_channel_ids else {}
        for ch in canales_resueltos:
            stream_id = ch.get("provider_id") or provider_map.get(ch.get("channel_id"))
            if stream_id:
                ch["provider_id"] = stream_id
                if client == "android":
                    ch["stream_url"] = f"{base_url}/live/{username}/{pwd}/{stream_id}"
                elif not ch.get("stream_url"):
                    ch["stream_url"] = f"{base_url}/{username}/{pwd}/{stream_id}"

    return CalendarEvent(
        id=str(evento["id"]),
        fecha=evento.get("fecha"),
        hora=evento.get("hora"),
        competicion=evento.get("competicion"),
        subtitulo_competicion=evento.get("subtitulo_competicion"),
        categoria=evento.get("categoria"),
        equipos=evento.get("equipos"),
        imagen_evento=evento.get("imagen_evento"),
        canales_original=evento.get("canales_original", []) or [],
        canales_resueltos=canales_resueltos,
    )


@app.get("/api/series/{serie_name}/episodes", tags=["Content"])
async def get_serie_episodes(
    serie_name: str,
    request: Request,
    page: int | None = Query(None, ge=1, description="Número de página"),
    page_size: int | None = Query(None, ge=1, le=100, description="Items por página"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    if "page" not in request.query_params and "page_size" not in request.query_params:
        episodes = content_svc.get_episodes_by_serie_name(
            serie_name=serie_name,
            username=auth.username,
            password=password or "",
        )

        if not episodes:
            raise NotFoundException("Serie", serie_name)

        return {
            "serie_name": serie_name,
            "total_episodes": len(episodes),
            "seasons": list(set([ep.get("temporada") for ep in episodes if ep.get("temporada")])),
            "episodes": episodes,
        }

    episodes = content_svc.get_episodes_by_serie_name_paginated(
        serie_name=serie_name,
        username=auth.username,
        password=password or "",
        page=page or 1,
        page_size=page_size or 50,
    )

    if episodes.get("total", 0) == 0:
        raise NotFoundException("Serie", serie_name)

    return episodes


# ============================================
# API: Watch Progress
# ============================================


@app.get("/api/watch-progress", tags=["Watch Progress"])
async def get_continue_watching(
    limit: int = Query(20, ge=1, le=50, description="Máximo de items"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene items con progreso de visualización incompleto. Requiere Bearer Token."""
    items = wp_svc.get_continue_watching(auth.user_id, limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/watch-progress/watched", tags=["Watch Progress"])
async def get_watched_items(
    limit: int = Query(100, ge=1, le=500, description="Máximo de items"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene items marcados como vistos. Requiere Bearer Token."""
    items = wp_svc.get_watched_items(auth.user_id, limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/api/watch-progress/{content_id}", tags=["Watch Progress"])
async def get_watch_progress(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene el progreso de un item específico. Requiere Bearer Token."""
    progress = wp_svc.get_progress(auth.user_id, content_id)
    if not progress:
        raise NotFoundException("WatchProgress", content_id)
    return progress


@app.put("/api/watch-progress/{content_id}", tags=["Watch Progress"])
async def upsert_watch_progress(
    content_id: str,
    body: WatchProgressUpsert,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Crea o actualiza el progreso de visualización. Requiere Bearer Token."""
    result = wp_svc.upsert_progress(auth.user_id, content_id, body.model_dump())
    return result


@app.delete("/api/watch-progress/{content_id}", tags=["Watch Progress"])
async def delete_watch_progress(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Elimina el progreso de visualización de un item. Requiere Bearer Token."""
    deleted = wp_svc.delete_progress(auth.user_id, content_id)
    if not deleted:
        raise NotFoundException("WatchProgress", content_id)
    return {"deleted": True}


@app.post("/api/watch-progress/{content_id}/mark-watched", tags=["Watch Progress"])
async def mark_watched(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Marca un contenido como visto. Requiere Bearer Token."""
    result = wp_svc.set_is_watched(auth.user_id, content_id, True)
    return {"content_id": content_id, "is_watched": True, "result": result}


@app.post("/api/watch-progress/{content_id}/mark-unwatched", tags=["Watch Progress"])
async def mark_unwatched(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Marca un contenido como no visto. Requiere Bearer Token."""
    result = wp_svc.set_is_watched(auth.user_id, content_id, False)
    return {"content_id": content_id, "is_watched": False, "result": result}


@app.get("/api/watch-progress/{content_id}/status", tags=["Watch Progress"])
async def get_watch_status(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene el estado de visto de un contenido. Requiere Bearer Token."""
    progress = wp_svc.get_progress(auth.user_id, content_id)
    if not progress:
        return {"content_id": content_id, "is_watched": False, "progress_percent": 0}
    return {
        "content_id": content_id,
        "is_watched": progress.get("is_watched", False),
        "progress_percent": progress.get("progress_percent", 0),
    }


# ============================================
# API: Playlist M3U
# ============================================
# API: Playlist M3U
# ============================================


@app.get("/get.php", tags=["Playlist"])
async def get_playlist_standard(
    request: Request,
    username: str = Query(..., description="Usuario"),
    password: str = Query(..., description="Contraseña"),
    type: str | None = Query(None, description="Tipo: m3u, m3u_plus"),
    _output: str | None = Query(None, description="Output: ts, m3u8"),
    content: str = Query("full", description="Contenido: full, live, movie, series"),
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    playlist_svc: PlaylistServiceV2 = Depends(get_playlist_service_v2),
):
    """
    Genera playlist M3U — Formato estándar de proveedores IPTV.
    """
    valid_content = ["full", "live", "movie", "series"]
    if content not in valid_content:
        content = "full"

    auth = await asyncio.to_thread(user_svc.validate_credentials, username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

    user_agent = request.headers.get("User-Agent", "Unknown")
    ip_address = request.client.host if request.client else "Unknown"

    success, message, _ = await asyncio.to_thread(
        device_svc.register_or_update_session,
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices,
    )

    if not success:
        raise TooManyRequestsException(message)

    logger.info(f"📋 Playlist solicitada: user={username}, content={content}, ua={user_agent[:50]}")

    m3u_content = playlist_svc.generate_m3u(
        username=username, password=password, content_type=content
    )

    content_bytes = m3u_content.encode("utf-8")

    content_length = len(content_bytes)

    SIZE_THRESHOLD = 5 * 1024 * 1024
    if content_length > SIZE_THRESHOLD:
        content_bytes = gzip.compress(content_bytes, compresslevel=6)
        is_gzip = True
    else:
        is_gzip = False

    content_length = len(content_bytes)

    filename = (
        f"playlist_{username}_{content}.m3u" if content != "full" else f"playlist_{username}.m3u"
    )

    headers = {
        "Content-Length": str(content_length),
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Description": "File Transfer",
        "Cache-Control": "must-revalidate",
        "Pragma": "public",
        "Expires": "0",
    }

    if is_gzip:
        headers["Content-Encoding"] = "gzip"

    return Response(content=content_bytes, media_type="application/octet-stream", headers=headers)


# ============================================
# Endpoints HLS — sirven ficheros generados por ffmpeg
# ============================================


@app.get("/hls/{session_id}/playlist.m3u8", tags=["HLS"])
async def hls_playlist(
    session_id: str, transcode_svc: TranscodeService = Depends(get_transcode_service)
):
    """Sirve el playlist .m3u8 de una sesión HLS activa."""
    file_path = transcode_svc.get_file_path(session_id, "playlist.m3u8")
    if not file_path:
        raise NotFoundException("Sesión HLS", session_id)

    return FileResponse(
        path=file_path,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-cache, no-store",
        },
    )


@app.get("/hls/{session_id}/{segment}", tags=["HLS"])
async def hls_segment(
    request: Request,
    session_id: str,
    segment: str,
    transcode_svc: TranscodeService = Depends(get_transcode_service),
):
    """Sirve un segmento .ts de una sesión HLS activa."""
    if "/" in segment or ".." in segment:
        raise BadRequestException("Segmento inválido")

    if not segment.endswith(".ts"):
        raise BadRequestException("Segmento inválido")

    file_path = transcode_svc.get_file_path(session_id, segment)
    if not file_path:
        raise NotFoundException("Segmento", segment)

    return FileResponse(
        path=file_path,
        media_type="video/mp2t",
        headers={
            "Cache-Control": "no-cache",
            **_build_cast_cors_headers(request),
        },
    )


def _build_cast_cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if not origin:
        origin = "https://www.gstatic.com"

    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Accept-Encoding, Range, Origin",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges, Content-Type",
        "Vary": "Origin",
    }


def _build_cast_options_response(request: Request) -> Response:
    return Response(status_code=204, headers=_build_cast_cors_headers(request))


def _build_cast_playlist_response(
    session_id: str, request: Request, transcode_svc: TranscodeService
) -> PlainTextResponse:
    file_path = transcode_svc.get_file_path(session_id, "playlist.m3u8")
    if not file_path:
        raise NotFoundException("Sesión HLS", session_id)

    with open(file_path, "r", encoding="utf-8") as f:
        playlist_content = f.read()

    forwarded_proto = request.headers.get("x-forwarded-proto", "https").split(",")[0].strip()
    forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""

    internal_hosts = ["iptv-api", "localhost", "127.0.0.1"]
    is_internal = any(h in forwarded_host for h in internal_hosts)

    if forwarded_host and not is_internal:
        base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")
    else:
        base_url = settings.public_domain.rstrip("/")

    if base_url.startswith("http://"):
        base_url = "https://" + base_url[len("http://") :]

    logger.info(
        f"📺 Cast playlist base_url={base_url}, session={session_id}, "
        f"forwarded_host={forwarded_host}, proto={forwarded_proto}"
    )

    rewritten_lines = []
    for line in playlist_content.splitlines():
        if line and not line.startswith("#"):
            rewritten_lines.append(f"{base_url}/cast/hls/{session_id}/{line}")
        else:
            rewritten_lines.append(line)

    return PlainTextResponse(
        content="\n".join(rewritten_lines),
        media_type="application/x-mpegURL",
        headers={
            "Cache-Control": "no-cache, no-store",
            **_build_cast_cors_headers(request),
        },
    )


@app.get("/cast/hls/{session_id}/{segment}", tags=["HLS", "Chromecast"])
async def cast_hls_segment(
    request: Request,
    session_id: str,
    segment: str,
    transcode_svc: TranscodeService = Depends(get_transcode_service),
):
    """Sirve segmentos HLS para Chromecast."""
    logger.info(f"📺 Chromecast segment: session={session_id}, segment={segment}")
    return await hls_segment(
        request=request,
        session_id=session_id,
        segment=segment,
        transcode_svc=transcode_svc,
    )


@app.options("/cast/hls/{session_id}/{segment}", tags=["HLS", "Chromecast"])
async def cast_hls_segment_options(session_id: str, segment: str, request: Request):
    return _build_cast_options_response(request)


@app.get("/api/video-extract", tags=["Video Extractor"])
async def extract_video_get(
    url: str = Query(..., description="URL del proveedor a extraer"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    extractor: VideoExtractorService = Depends(get_video_extractor),
):
    """
    Extrae la URL directa de reproducción a partir de una URL de embed.

    Proveedores soportados: streamtape, stape, netu, streamhide, vidhide,
    streamwish, filelions, vidmoly, doodstream, filemoon, mp4upload,
    okru, uqload, upstream, voe, lulustream.

    Requiere Bearer Token.
    """
    provider = extractor.detect_provider(url)
    if not provider:
        raise BadRequestException(
            f"Proveedor no soportado. URL: {url[:100]}. "
            f"Soportados: {', '.join(extractor.supported_providers())}"
        )

    try:
        result = await extractor.extract(url)
        return {
            "success": True,
            "provider": result["provider"],
            "type": result["type"],
            "url": result["url"],
            "sources": result.get("sources"),
            "required_headers": result.get("required_headers"),
            # None si el proveedor no da múltiples calidades
        }
    except ValueError as e:
        raise BadRequestException(str(e)) from e
    except Exception as e:
        logger.error(f"[/api/video-extract] Error inesperado: {e}")
        raise HTTPException(status_code=502, detail=f"Error al extraer video: {e!s}") from e


@app.post("/api/video-extract/multi", tags=["Video Extractor"])
async def extract_video_multi(
    body: ExtractMultiRequest,
    auth: AuthDep = Depends(require_auth_with_jwt),
    extractor: VideoExtractorService = Depends(get_video_extractor),
):
    """
    Extrae múltiples URLs en paralelo (máximo 10).
    Útil cuando un episodio tiene varios espejos/proveedores.

    Body: { "urls": ["https://streamtape.com/e/...", "https://netu.ac/e/..."] }
    Requiere Bearer Token.
    """
    if len(body.urls) > 10:
        raise BadRequestException("Máximo 10 URLs por petición")

    if not body.urls:
        raise BadRequestException("Debes proporcionar al menos una URL")

    results = await extractor.extract_multi(body.urls)

    successes = [r for r in results if not r.get("error")]
    failures = [r for r in results if r.get("error")]

    return {
        "total": len(results),
        "success_count": len(successes),
        "failure_count": len(failures),
        "results": results,
    }


# ============================================
# API: Stream Proxy
# ============================================


async def _proxy_stream_handler(
    content_type: str,
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserServiceV2,
    device_svc: DeviceServiceV2,
    stream_svc: StreamProxyServiceV2,
    transcode_svc: TranscodeService,
    force_hls_for_live: bool = False,
    hls_profile: str = "web",
):
    """Handler interno para proxy de streams."""
    if content_type not in ["live", "movie", "series"]:
        raise BadRequestException(
            "Tipo de stream inválido", {"valid_types": ["live", "movie", "series"]}
        )

    auth = await asyncio.to_thread(user_svc.validate_credentials, username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

    user_agent = request.headers.get("User-Agent", "Unknown")
    ip_address = request.client.host if request.client else "Unknown"

    success, message, _ = await asyncio.to_thread(
        device_svc.register_or_update_session,
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices,
    )

    if not success:
        raise TooManyRequestsException(message)

    clean_stream_id = stream_id.split(".")[0]
    original_url = stream_svc.get_original_url(clean_stream_id, content_type)

    logger.info(
        f"🎬 STREAM REQUEST: type={content_type}, user={username}, stream_id={clean_stream_id}, url={original_url[:60] if original_url else 'NOT FOUND'}..."
    )

    if not original_url:
        raise NotFoundException("Stream", stream_id)

    origin = request.headers.get("origin") or request.headers.get("referer", "")
    is_from_allowed_web = any(orig in origin for orig in ALLOWED_WEB_ORIGINS if origin)
    logger.info(
        f"🌐 Stream routing: type={content_type}, user={username}, "
        f"allowed_web={is_from_allowed_web}, bootstrap_proxy_all=true, "
        f"origin={origin[:100] if origin else 'none'}"
    )

    should_use_hls = (is_from_allowed_web or force_hls_for_live) and content_type == "live"

    if should_use_hls and transcode_svc:
        hls_source_url = original_url
        resolved_hls_url = await stream_svc.resolve_redirects(
            original_url, use_cache=False, use_proxy=True
        )
        logger.info(
            f"🎯 HLS bootstrap resolve: stream_id={clean_stream_id}, "
            f"changed={resolved_hls_url != original_url}, forced_hls={force_hls_for_live}"
        )
        hls_source_url = resolved_hls_url

        session = await transcode_svc.get_or_create_session(
            username, clean_stream_id, hls_source_url, profile=hls_profile
        )
        ready = await transcode_svc.wait_for_playlist(session)
        if not ready:
            raise BadRequestException("El stream no está disponible o tardó demasiado en arrancar")
        logger.info(f"🎬 HLS redirect: session={session.session_id}")
        return RedirectResponse(url=f"/hls/{session.session_id}/playlist.m3u8", status_code=302)

    use_cache_for_redirect = content_type != "live"
    stream_url = await stream_svc.resolve_redirects(
        original_url, use_cache=use_cache_for_redirect, use_proxy=True
    )
    if stream_url != original_url:
        logger.info(
            f"🎯 Bootstrap con proxy aplicado ({content_type}): "
            f"{original_url[:60]}... -> {stream_url[:60]}..."
        )
    else:
        logger.info(f"🎯 Bootstrap con proxy sin cambio ({content_type}): {original_url[:60]}...")

    request_headers = {}
    if content_type in ["movie", "series"]:
        range_header = request.headers.get("range")
        if range_header:
            request_headers["Range"] = range_header

    try:
        status_code, headers, body = await stream_svc.get_stream_response(
            stream_url, headers=request_headers
        )

        if isinstance(body, str):
            from fastapi.responses import PlainTextResponse

            return PlainTextResponse(
                content=body,
                status_code=status_code,
                headers=headers,
                media_type=headers.get("content-type", "application/vnd.apple.mpegurl"),
            )

        return StreamingResponse(
            body,
            status_code=status_code,
            headers=headers,
            media_type=headers.get("content-type", "video/mp2t"),
        )
    except Exception as e:
        raise BadRequestException(f"Error al obtener stream: {e!s}") from e


# ============================================
# IMPORTANTE: rutas genéricas de streams VAN DESPUÉS de todas las
# rutas /api/* para evitar colisiones. Además, validamos que no
# capturen paths que empiezan por "api" u otros prefijos reservados.
# ============================================

_RESERVED_PREFIXES = {
    "api",
    "cast",
    "hls",
    "auth",
    "internal",
    "logo",
    "get.php",
    "health",
}


@app.get("/{content_type}/{username}/{password}/{stream_id}", tags=["Stream"])
async def proxy_stream_content(
    content_type: str,
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
    transcode_svc: TranscodeService = Depends(get_transcode_service),
):
    """
    Proxy de streams para live, movie y series.
    Formato: /{live|movie|series}/{username}/{password}/{stream_id}
    """
    if content_type in _RESERVED_PREFIXES:
        logger.warning(
            f"[DIAG] Catch-all 4-seg BLOCKED: content_type={content_type}, user={username}, stream_id={stream_id}"
        )
        raise BadRequestException(f"Ruta no válida: '{content_type}' es un prefijo reservado")
    logger.info(
        f"[DIAG] Catch-all 4-seg ENTERED: /{content_type}/{username}/{password}/{stream_id}"
    )
    return await _proxy_stream_handler(
        content_type=content_type,
        username=username,
        password=password,
        stream_id=stream_id,
        request=request,
        user_svc=user_svc,
        device_svc=device_svc,
        stream_svc=stream_svc,
        transcode_svc=transcode_svc,
        force_hls_for_live=False,
        hls_profile="web",
    )


@app.get("/{username}/{password}/{stream_id}", tags=["Stream"])
async def proxy_stream_channel(
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
    transcode_svc: TranscodeService = Depends(get_transcode_service),
):
    """Proxy de streams para canales en vivo (sin tipo en URL)."""
    if username in _RESERVED_PREFIXES or password in _RESERVED_PREFIXES:
        logger.warning(
            f"[DIAG] Catch-all 3-seg BLOCKED: username={username}, password={password}, stream_id={stream_id}"
        )
        raise BadRequestException(
            f"Ruta no válida: '{username}' o '{password}' son prefijos reservados"
        )
    logger.info(f"[DIAG] Catch-all 3-seg ENTERED: /{username}/{password}/{stream_id}")
    return await _proxy_stream_handler(
        content_type="live",
        username=username,
        password=password,
        stream_id=stream_id,
        request=request,
        user_svc=user_svc,
        device_svc=device_svc,
        stream_svc=stream_svc,
        transcode_svc=transcode_svc,
        force_hls_for_live=True,
        hls_profile="web",
    )


@app.get(
    "/cast/live/{username}/{password}/{stream_id}/playlist.m3u8",
    tags=["Stream", "Chromecast"],
)
async def proxy_stream_channel_chromecast(
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
    transcode_svc: TranscodeService = Depends(get_transcode_service),
):
    """Genera una playlist HLS compatible con Chromecast para canales en vivo."""
    logger.info(f"📺 Chromecast request: user={username}, stream_id={stream_id}")
    auth = await asyncio.to_thread(user_svc.validate_credentials, username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

    user_agent = request.headers.get("User-Agent", "Unknown")
    ip_address = request.client.host if request.client else "Unknown"

    success, message, _ = await asyncio.to_thread(
        device_svc.register_or_update_session,
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices,
    )

    if not success:
        raise TooManyRequestsException(message)

    clean_stream_id = stream_id.split(".")[0]
    original_url = stream_svc.get_original_url(clean_stream_id, "live")

    if not original_url:
        raise NotFoundException("Stream", stream_id)

    hls_source_url = await stream_svc.resolve_redirects(
        original_url, use_cache=False, use_proxy=True
    )

    session = await transcode_svc.get_or_create_session(
        username, clean_stream_id, hls_source_url, profile="chromecast"
    )
    ready = await transcode_svc.wait_for_playlist(session)
    if not ready:
        raise BadRequestException("El stream no está disponible o tardó demasiado en arrancar")

    logger.info(f"📺 Chromecast media playlist ready: session={session.session_id}")
    return _build_cast_playlist_response(session.session_id, request, transcode_svc)


@app.options(
    "/cast/live/{username}/{password}/{stream_id}/playlist.m3u8",
    tags=["Stream", "Chromecast"],
)
async def proxy_stream_channel_chromecast_options(
    username: str,
    password: str,
    stream_id: str,
    request: Request,
):
    return _build_cast_options_response(request)


@app.get(
    "/cast/{username}/{password}/{stream_id}/playlist.m3u8",
    tags=["Stream", "Chromecast"],
)
async def proxy_stream_channel_chromecast_shortcut(
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
    transcode_svc: TranscodeService = Depends(get_transcode_service),
):
    """Atajo Chromecast para live, alineado con la ruta web sin /live."""
    return await proxy_stream_channel_chromecast(
        username=username,
        password=password,
        stream_id=stream_id,
        request=request,
        user_svc=user_svc,
        device_svc=device_svc,
        stream_svc=stream_svc,
        transcode_svc=transcode_svc,
    )


@app.options(
    "/cast/{username}/{password}/{stream_id}/playlist.m3u8",
    tags=["Stream", "Chromecast"],
)
async def proxy_stream_channel_chromecast_shortcut_options(
    username: str,
    password: str,
    stream_id: str,
    request: Request,
):
    return _build_cast_options_response(request)


# ============================================
# API: Stream Validation (Nginx)
# ============================================


@app.get(
    "/auth/validate-stream/{content_type}/{username}/{password}/{provider_id}",
    tags=["Stream Validation"],
)
async def validate_stream(
    content_type: str,
    username: str,
    password: str,
    provider_id: str,
    request: Request,
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
):
    """Valida credenciales y devuelve URL original para nginx auth_request."""
    auth = await asyncio.to_thread(user_svc.validate_credentials, username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

    user_agent = request.headers.get("User-Agent", "Unknown")
    ip_address = request.client.host if request.client else "Unknown"

    success, message, _ = await asyncio.to_thread(
        device_svc.register_or_update_session,
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices,
    )

    if not success:
        raise TooManyRequestsException(message)

    clean_provider_id = provider_id.split(".")[0]
    original_url = stream_svc.get_original_url(clean_provider_id, content_type)

    if not original_url:
        raise NotFoundException("Stream", provider_id)

    final_url = await stream_svc.resolve_redirects(
        original_url, use_cache=content_type != "live", use_proxy=True
    )

    return PlainTextResponse(
        content="OK",
        headers={"X-Original-Url": final_url, "X-Provider-Id": clean_provider_id},
    )


# ============================================
# API: Internal Stream URL (for Nginx direct proxy)
# ============================================


@app.get("/internal/stream-url", tags=["Internal"])
async def get_stream_url_internal(
    request: Request,
    user: str = Query(...),
    password: str = Query(...),
    id: str = Query(...),
    type: str = Query("live", description="Tipo de contenido: live, movie, series"),
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
):
    """
    Endpoint interno para obtener URL de stream.
    Devuelve redirect 307 al stream del proveedor para proxy directo via nginx.
    """
    auth = await asyncio.to_thread(user_svc.validate_credentials, user, password)

    if not auth.valid or not auth.can_connect:
        raise UnauthorizedException("Credenciales inválidas")

    user_agent = request.headers.get("User-Agent", "Unknown")
    ip_address = request.client.host if request.client else "Unknown"

    success, message, _ = await asyncio.to_thread(
        device_svc.register_or_update_session,
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices,
    )

    if not success:
        raise TooManyRequestsException(message)

    clean_id = id.split(".")[0]

    content_type_map = {"live": "live", "movie": "movie", "series": "series"}
    content_type = content_type_map.get(type, "live")

    original_url = stream_svc.get_original_url(clean_id, content_type)

    if not original_url:
        raise NotFoundException("Stream", id)

    print(f"[DEBUG] original_url: {original_url}")

    use_cache = content_type != "live"
    final_url = await stream_svc.resolve_redirects(
        original_url, use_cache=use_cache, use_proxy=True
    )

    print(f"[DEBUG] final_url: {final_url}")
    print(f"[DEBUG] son_iguales: {original_url == final_url}")

    return RedirectResponse(url=final_url, status_code=307)


# ============================================
# API: Logo/Image Proxy
# ============================================


@app.get("/logo", tags=["Logo"])
async def proxy_logo(
    url: str = Query(..., description="URL original del logo"),
    type: str = Query("channel", description="Tipo: channel, movie, series"),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
):
    """Proxy de imágenes/logos para resolver Mixed Content."""
    from urllib.parse import unquote

    import httpx

    try:
        original_url = unquote(url)
    except Exception:
        original_url = url

    if not original_url.startswith("http"):
        original_url = f"http://{original_url}"

    placeholder_map = {
        "movie": "movies.png",
        "series": "series.png",
        "channel": "channels.png",
    }
    placeholder_filename = placeholder_map.get(type, "channels.png")

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(
                original_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                },
            )
            response.raise_for_status()

            content_type = response.headers.get("content-type", "image/jpeg")

            return Response(
                content=response.content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",
                },
            )
    except Exception:
        pass

    from pathlib import Path

    placeholder_path = Path(__file__).parent.parent / "resources" / "images" / placeholder_filename
    if placeholder_path.exists():
        with open(placeholder_path, "rb") as f:
            placeholder_content = f.read()
        return Response(
            content=placeholder_content,
            media_type="image/png",
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Fallback": "true",
            },
        )

    return Response(content=b"", status_code=204)


# ============================================
# Main
# ============================================

if __name__ == "__main__":
    uvicorn.run("api:app", host="0.0.0.0", port=3010, reload=True)
