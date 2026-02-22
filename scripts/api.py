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
import logging
from contextlib import asynccontextmanager

from starlette.responses import RedirectResponse

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)

logger = logging.getLogger("iptv-api")

from datetime import datetime, timedelta
from typing import Optional

logging.getLogger("httpx").setLevel(logging.DEBUG)

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Query, Depends, Header, status, Response
from fastapi.middleware.cors import CORSMiddleware
# ✏️ CAMBIO 1: añadido FileResponse para servir ficheros HLS
from fastapi.responses import PlainTextResponse, StreamingResponse, FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt

from services import UserService, DeviceService, PlaylistService, StreamProxyService, ContentService, CalendarService
from services.transcode_service import TranscodeService
from services.postgres_service import get_postgres_service
from utils.config import get_settings
from utils.models import UserCreate, UserUpdate, ValidateCredentials, AuthResult, SystemStats, Token, CalendarDayResponse, CalendarEvent
from utils.constants import JWT_ALGORITHM, JWT_ACCESS_TOKEN_EXPIRE_MINUTES
from utils.exceptions import (
    NotFoundException, UnauthorizedException, ForbiddenException,
    BadRequestException, ConflictException, TooManyRequestsException
)
from utils.dependencies import (
    set_services, get_user_service, get_device_service, get_playlist_service,
    get_stream_service, get_content_service, get_transcode_service, get_calendar_service, get_current_user, require_admin,
    require_auth_with_credentials, require_auth_with_session, require_auth_with_jwt,
    AuthResult as AuthDep
)
from scripts.xtream_router import router as xtream_router

# Configuración
settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = JWT_ACCESS_TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# ✏️ CAMBIO 2: constante con los origins permitidos (reutilizada en _proxy_stream_handler)
ALLOWED_WEB_ORIGINS = ['https://walactvweb.walerike.com', 'http://localhost:4200']


# ============================================
# Ciclo de Vida
# ============================================

async def cleanup_sessions_task():
    """Tarea periódica para limpiar sesiones inactivas"""
    while True:
        try:
            await asyncio.sleep(settings.cleanup_interval_minutes * 60)
            device_svc = get_device_service()
            cleaned = device_svc.cleanup_inactive_sessions()
            if cleaned > 0:
                print(f"🧹 Limpiadas {cleaned} sesiones inactivas")
        except Exception as e:
            print(f"❌ Error en limpieza de sesiones: {e}")


# ✏️ CAMBIO 3: nueva tarea de limpieza de sesiones HLS
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
        supabase_client = settings.get_supabase_client()
        user_svc = UserService(supabase_client)
        device_svc = DeviceService(supabase_client)
        playlist_svc = PlaylistService(supabase_client)
        stream_svc = StreamProxyService(supabase_client)
        content_svc = ContentService(supabase_client)
        transcode_svc = TranscodeService()

        pg_svc = get_postgres_service()
        calendar_svc = CalendarService(pg_svc)

        set_services(user_svc, device_svc, playlist_svc, stream_svc, content_svc, transcode_svc, calendar_svc)

        stream_svc.preload_cache()
        asyncio.create_task(cleanup_sessions_task())
        asyncio.create_task(cleanup_hls_task())  # ✏️ CAMBIO 3b: arrancar tarea HLS
        print("✅ IPTV API iniciada correctamente")

    yield

    print("🛑 Cerrando IPTV API...")
    # ✏️ CAMBIO 3c: parar todos los procesos ffmpeg al cerrar
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
    lifespan=lifespan
)

# Xtream Codes API (compatibilidad con reproductores IPTV)
app.include_router(xtream_router)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://walactvweb.walerike.com",
        "http://localhost:4200"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Manejador global que asegura cabeceras CORS en errores"""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={"error": "Internal Server Error", "message": str(exc)}
    )


# ============================================
# Funciones de Utilidad
# ============================================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Crea un token JWT de acceso"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


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
    svc: UserService = Depends(get_user_service)
):
    """Endpoint de Login. Retorna JWT token."""
    user = svc.get_user_by_username(form_data.username)

    if not user:
        raise UnauthorizedException("Usuario o contraseña incorrectos")

    if not svc._verify_password(form_data.password, user['password_hash']):
        raise UnauthorizedException("Usuario o contraseña incorrectos")

    if not user['is_active']:
        raise ForbiddenException("Usuario inactivo")

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user['id'], "role": user.get('role', 'user')},
        expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": user.get('role', 'user')
    }


# ============================================
# API: Usuarios (Admin)
# ============================================

@app.post("/api/admin/users", response_model=dict, tags=["Admin - Users"])
async def create_user(
    user_data: UserCreate,
    _: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service)
):
    """Crear nuevo usuario (Solo Admin)"""
    try:
        return svc.create_user(user_data)
    except ValueError as e:
        raise BadRequestException(str(e))


@app.get("/api/admin/users", response_model=dict, tags=["Admin - Users"])
async def list_users(
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(100, ge=1, le=1000, description="Items por página"),
    _: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service)
):
    """Listar usuarios paginados (Solo Admin)"""
    skip = (page - 1) * page_size
    users = svc.list_users(skip, page_size)

    all_users = svc.list_users(0, 10000)
    total = len(all_users)
    pages = (total + page_size - 1) // page_size

    return {
        "items": users,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "has_next": page < pages,
        "has_prev": page > 1
    }


@app.get("/api/admin/users/{user_id}", response_model=dict, tags=["Admin - Users"])
async def get_user(
    user_id: str,
    _: dict = Depends(require_admin),
    svc: UserService = Depends(get_user_service)
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
    svc: UserService = Depends(get_user_service)
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
    svc: UserService = Depends(get_user_service)
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
    svc: DeviceService = Depends(get_device_service)
):
    """Obtiene dispositivos de un usuario"""
    return svc.get_user_devices(user_id)


@app.delete("/api/admin/users/{user_id}/devices/{device_id}", tags=["Admin - Devices"])
async def disconnect_device(
    user_id: str,
    device_id: str,
    _: dict = Depends(require_admin),
    svc: DeviceService = Depends(get_device_service)
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
    svc: DeviceService = Depends(get_device_service)
):
    """Desconecta todos los dispositivos de un usuario"""
    count = svc.disconnect_all_devices(user_id)
    return {"message": f"{count} dispositivos desconectados"}


@app.get("/api/admin/sessions", tags=["Admin - Devices"])
async def get_all_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    _: dict = Depends(require_admin),
    svc: DeviceService = Depends(get_device_service)
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
        "pages": pages
    }


# ============================================
# API: Stats (Admin)
# ============================================

@app.get("/api/admin/stats", response_model=SystemStats, tags=["Admin - Stats"])
async def get_system_stats(
    _: dict = Depends(require_admin),
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    playlist_svc: PlaylistService = Depends(get_playlist_service)
):
    """Obtiene estadísticas del sistema"""
    users = user_svc.list_users(limit=10000)
    sessions = device_svc.get_all_sessions(limit=10000)
    playlist_stats = playlist_svc.get_playlist_stats()

    active_users = sum(1 for u in users if u.get('is_active', False))

    return SystemStats(
        total_users=len(users),
        active_users=active_users,
        total_sessions=len(sessions),
        total_channels=playlist_stats['total_channels'],
        total_movies=playlist_stats['total_movies'],
        total_series=playlist_stats['total_series']
    )


@app.get("/api/admin/resilience", tags=["Admin - Stats"])
async def get_resilience_status(
    _: dict = Depends(require_admin),
    stream_svc: StreamProxyService = Depends(get_stream_service)
):
    """Obtiene el estado de resiliencia de streams (circuit breaker, retry, buffer)"""
    return stream_svc.get_resilience_status()


# ============================================
# API: Contenido (Público)
# ============================================

@app.get("/api/content/groups", tags=["Content"])
async def get_groups_public(
    content_type: str = Query('channels', enum=['channels', 'movies', 'series']),
    countries: Optional[str] = Query(None, description="Filtrar por países (separados por coma: US,MX,ES)"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """Obtiene grupos disponibles por tipo de contenido. Requiere Bearer Token."""
    country_list = None
    if countries:
        country_list = [c.strip().upper() for c in countries.split(',') if c.strip()]

    return {"groups": content_svc.get_groups(content_type, country_list)}


@app.get("/api/content/countries", tags=["Content"])
async def get_countries_public(
    content_type: str = Query('channels', enum=['channels', 'movies', 'series']),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """Obtiene países disponibles por tipo de contenido (requiere Bearer Token)"""
    return {"countries": content_svc.get_countries(content_type)}


@app.post("/api/admin/content/reload", tags=["Admin - Content"])
async def reload_template(
    _: dict = Depends(require_admin),
    playlist_svc: PlaylistService = Depends(get_playlist_service)
):
    """Recarga los templates M3U en memoria"""
    playlist_svc.reload_template()
    templates = playlist_svc._templates
    if templates.get('full'):
        return {
            "status": "success",
            "message": "Templates recargados correctamente",
            "templates": {
                k: len(v) if v else 0 for k, v in templates.items()
            }
        }
    else:
        raise BadRequestException("No se pudo recargar el template. Verifica que playlist_template.m3u exista.")


# ============================================
# API: Contenido (Público - Requiere Auth)
# ============================================

@app.get("/api/content", tags=["Content"])
async def get_content(
    content_type: str = Query(..., enum=['channels', 'movies', 'series'], description="Tipo de contenido"),
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(50, ge=1, le=100, description="Items por página"),
    group: Optional[str] = Query(None, description="Filtrar por grupo"),
    country: Optional[str] = Query(None, description="Filtrar por país"),
    search: Optional[str] = Query(None, description="Buscar por nombre"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """Obtiene lista paginada de contenido. Requiere Bearer Token."""
    return content_svc.get_content_list(
        content_type=content_type,
        page=page,
        page_size=page_size,
        group=group,
        country=country,
        search=search,
        username=auth.username,
        password=''
    )


@app.get("/api/content/{content_type}/{item_id}", tags=["Content"])
async def get_content_item(
    content_type: str,
    item_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """Obtiene un item específico de contenido. Requiere Bearer Token."""
    if content_type not in ['channels', 'movies', 'series']:
        raise BadRequestException("Tipo de contenido inválido", {"valid_types": ["channels", "movies", "series"]})

    item = content_svc.get_content_item(
        content_type=content_type,
        item_id=item_id,
        username=auth.username,
        password=''
    )

    if not item:
        content_name = {"channels": "Canal", "movies": "Película", "series": "Serie"}[content_type]
        raise NotFoundException(content_name, item_id)

    return item


@app.get("/api/content/stats", tags=["Content"])
async def get_content_stats(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """Obtiene el total de canales, películas y series disponibles. Requiere Bearer Token."""
    counts = content_svc.get_content_count()
    return {
        "channels": counts['channels'],
        "movies": counts['movies'],
        "series": counts['series'],
        "total": counts['channels'] + counts['movies'] + counts['series']
    }


# ============================================
# API: Calendar
# ============================================

@app.get("/api/calendar/{fecha}", response_model=CalendarDayResponse, tags=["Calendar"])
async def get_calendar_by_date(
    fecha: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    calendar_svc=Depends(get_calendar_service)
):
    """Obtiene todos los eventos deportivos de una fecha. Requiere Bearer Token."""
    from datetime import datetime

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        raise BadRequestException("Formato de fecha inválido. Use YYYY-MM-DD")

    eventos_raw = calendar_svc.get_events_by_date(fecha)

    if not eventos_raw:
        return CalendarDayResponse(fecha=fecha, total_eventos=0, eventos=[])

    eventos = []
    for evento in eventos_raw:
        canales_resueltos = evento.get('canales_resueltos', []) or []
        eventos.append(CalendarEvent(
            id=str(evento['id']),
            fecha=evento['fecha'],
            hora=evento['hora'],
            competicion=evento.get('competicion'),
            categoria=evento.get('categoria'),
            equipos=evento['equipos'],
            canales_original=evento.get('canales_original', []) or [],
            canales_resueltos=canales_resueltos
        ))

    return CalendarDayResponse(fecha=fecha, total_eventos=len(eventos), eventos=eventos)


@app.get("/api/calendar/event/{event_id}", response_model=CalendarEvent, tags=["Calendar"])
async def get_calendar_event(
    event_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    calendar_svc=Depends(get_calendar_service)
):
    """Obtiene un evento específico por su ID. Requiere Bearer Token."""
    evento = calendar_svc.get_event_by_id(event_id)

    if not evento:
        raise NotFoundException("Evento", event_id)

    canales_resueltos = evento.get('canales_resueltos', []) or []
    return CalendarEvent(
        id=str(evento['id']),
        fecha=evento['fecha'],
        hora=evento['hora'],
        competicion=evento.get('competicion'),
        categoria=evento.get('categoria'),
        equipos=evento['equipos'],
        canales_original=evento.get('canales_original', []) or [],
        canales_resueltos=canales_resueltos
    )


@app.get("/api/series/{serie_name}/episodes", tags=["Content"])
async def get_serie_episodes(
    serie_name: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """Obtiene todos los episodios de una serie. Requiere Bearer Token."""
    episodes = content_svc.get_episodes_by_serie_name(
        serie_name=serie_name,
        username=auth.username,
        password=''
    )

    if not episodes:
        raise NotFoundException("Serie", serie_name)

    return {
        "serie_name": serie_name,
        "total_episodes": len(episodes),
        "seasons": list(set([ep.get('temporada') for ep in episodes if ep.get('temporada')])),
        "episodes": episodes
    }


# ============================================
# API: Playlist M3U
# ============================================

@app.get("/get.php", tags=["Playlist"])
async def get_playlist_standard(
    request: Request,
    username: str = Query(..., description="Usuario"),
    password: str = Query(..., description="Contraseña"),
    type: Optional[str] = Query(None, description="Tipo: m3u, m3u_plus"),
    output: Optional[str] = Query(None, description="Output: ts, m3u8"),
    content: str = Query('full', description="Contenido: full, live, movie, series"),
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    playlist_svc: PlaylistService = Depends(get_playlist_service)
):
    """
    Genera playlist M3U — Formato estándar de proveedores IPTV.
    
    Parámetro 'content':
        - full: Todo el contenido (por defecto)
        - live: Solo canales en vivo
        - movie: Solo películas
        - series: Solo series
    
    Nota: Para reproductores móviles, usar content=live reduce significativamente el tamaño.
    """
    valid_content = ['full', 'live', 'movie', 'series']
    if content not in valid_content:
        content = 'full'
    
    auth = user_svc.validate_credentials(username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip_address = request.client.host if request.client else 'Unknown'

    success, message, _ = device_svc.register_or_update_session(
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices
    )

    if not success:
        raise TooManyRequestsException(message)

    logger.info(f"📋 Playlist solicitada: user={username}, content={content}, ua={user_agent[:50]}")

    m3u_content = playlist_svc.generate_m3u(username=username, password=password, content_type=content)

    content_bytes = m3u_content.encode('utf-8')
    content_length = len(content_bytes)

    filename = f"playlist_{username}_{content}.m3u" if content != 'full' else f"playlist_{username}.m3u"

    return Response(
        content=content_bytes,
        media_type="application/x-mpegURL",
        headers={
            "Content-Length": str(content_length),
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Description": "File Transfer",
            "Cache-Control": "must-revalidate",
            "Pragma": "public",
            "Expires": "0",
            "Access-Control-Allow-Origin": "*",
        }
    )


# ============================================
# ✏️ CAMBIO 4: Endpoints HLS — sirven ficheros generados por ffmpeg
# ============================================

@app.get("/hls/{session_id}/playlist.m3u8", tags=["HLS"])
async def hls_playlist(
    session_id: str,
    transcode_svc: TranscodeService = Depends(get_transcode_service)
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
        }
    )


@app.get("/hls/{session_id}/{segment}", tags=["HLS"])
async def hls_segment(
    session_id: str,
    segment: str,
    transcode_svc: TranscodeService = Depends(get_transcode_service)
):
    """Sirve un segmento .ts de una sesión HLS activa."""
    if not segment.endswith(".ts") or "/" in segment or ".." in segment:
        raise BadRequestException("Segmento inválido")

    file_path = transcode_svc.get_file_path(session_id, segment)
    if not file_path:
        raise NotFoundException("Segmento", segment)

    return FileResponse(
        path=file_path,
        media_type="video/mp2t",
        headers={
            "Cache-Control": "no-cache",
        }
    )


# ============================================
# API: Stream Proxy
# ============================================

async def _proxy_stream_handler(
    content_type: str,
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserService,
    device_svc: DeviceService,
    stream_svc: StreamProxyService,
    transcode_svc: TranscodeService
):
    """Handler interno para proxy de streams."""
    if content_type not in ['live', 'movie', 'series']:
        raise BadRequestException("Tipo de stream inválido", {"valid_types": ["live", "movie", "series"]})

    auth = user_svc.validate_credentials(username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip_address = request.client.host if request.client else 'Unknown'

    success, message, _ = device_svc.register_or_update_session(
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices
    )

    if not success:
        raise TooManyRequestsException(message)

    clean_stream_id = stream_id.split('.')[0]
    original_url = stream_svc.get_original_url(clean_stream_id, content_type)

    logger.info(f"🎬 STREAM REQUEST: type={content_type}, user={username}, stream_id={clean_stream_id}, url={original_url[:60] if original_url else 'NOT FOUND'}...")

    if not original_url:
        raise NotFoundException("Stream", stream_id)

    # ✏️ CAMBIO 5: lógica HLS correcta — sesión ffmpeg en disco + redirect al playlist
    origin = request.headers.get('origin') or request.headers.get('referer', '')
    is_from_allowed_web = any(orig in origin for orig in ALLOWED_WEB_ORIGINS if origin)

    if is_from_allowed_web and transcode_svc:
        session = await transcode_svc.get_or_create_session(username, clean_stream_id, original_url)
        ready = await transcode_svc.wait_for_playlist(session)
        if not ready:
            raise BadRequestException("El stream no está disponible o tardó demasiado en arrancar")
        logger.info(f"🎬 HLS redirect: session={session.session_id}")
        return RedirectResponse(url=f"/hls/{session.session_id}/playlist.m3u8", status_code=302)

    # Desde reproductores externos: proxy directo (sin cambios)
    request_headers = {}
    if content_type in ['movie', 'series']:
        range_header = request.headers.get('range')
        if range_header:
            request_headers['Range'] = range_header

    try:
        status_code, headers, body = await stream_svc.get_stream_response(
            original_url,
            headers=request_headers
        )

        if isinstance(body, str):
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse(
                content=body,
                status_code=status_code,
                headers=headers,
                media_type=headers.get('content-type', 'application/vnd.apple.mpegurl')
            )

        return StreamingResponse(
            body,
            status_code=status_code,
            headers=headers,
            media_type=headers.get('content-type', 'video/mp2t')
        )
    except Exception as e:
        raise BadRequestException(f"Error al obtener stream: {str(e)}")


@app.get("/{content_type}/{username}/{password}/{stream_id}", tags=["Stream"])
async def proxy_stream_content(
    content_type: str,
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    stream_svc: StreamProxyService = Depends(get_stream_service),
    transcode_svc: TranscodeService = Depends(get_transcode_service)
):
    """
    Proxy de streams para live, movie y series.
    Formato: /{live|movie|series}/{username}/{password}/{stream_id}
    """
    return await _proxy_stream_handler(
        content_type=content_type,
        username=username,
        password=password,
        stream_id=stream_id,
        request=request,
        user_svc=user_svc,
        device_svc=device_svc,
        stream_svc=stream_svc,
        transcode_svc=transcode_svc
    )


@app.get("/{username}/{password}/{stream_id}", tags=["Stream"])
async def proxy_stream_channel(
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    stream_svc: StreamProxyService = Depends(get_stream_service),
    transcode_svc: TranscodeService = Depends(get_transcode_service)
):
    """Proxy de streams para canales en vivo (sin tipo en URL)."""
    return await _proxy_stream_handler(
        content_type='live',
        username=username,
        password=password,
        stream_id=stream_id,
        request=request,
        user_svc=user_svc,
        device_svc=device_svc,
        stream_svc=stream_svc,
        transcode_svc=transcode_svc
    )


# ============================================
# API: Stream Validation (Nginx)
# ============================================

@app.get("/auth/validate-stream/{content_type}/{username}/{password}/{provider_id}", tags=["Stream Validation"])
async def validate_stream(
    content_type: str,
    username: str,
    password: str,
    provider_id: str,
    request: Request,
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    stream_svc: StreamProxyService = Depends(get_stream_service)
):
    """Valida credenciales y devuelve URL original para nginx auth_request."""
    auth = user_svc.validate_credentials(username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip_address = request.client.host if request.client else 'Unknown'

    success, message, _ = device_svc.register_or_update_session(
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices
    )

    if not success:
        raise TooManyRequestsException(message)

    clean_provider_id = provider_id.split('.')[0]
    original_url = stream_svc.get_original_url(clean_provider_id, content_type)

    if not original_url:
        raise NotFoundException("Stream", provider_id)

    final_url = await stream_svc.resolve_redirects(original_url)

    return PlainTextResponse(
        content="OK",
        headers={
            "X-Original-Url": final_url,
            "X-Provider-Id": clean_provider_id
        }
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
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    stream_svc: StreamProxyService = Depends(get_stream_service)
):
    """
    Endpoint interno para obtener URL de stream.
    Devuelve redirect 307 al stream del proveedor para proxy directo via nginx.
    """
    auth = user_svc.validate_credentials(user, password)

    if not auth.valid or not auth.can_connect:
        raise UnauthorizedException("Credenciales inválidas")

    user_agent = request.headers.get('User-Agent', 'Unknown')
    ip_address = request.client.host if request.client else 'Unknown'

    success, message, _ = device_svc.register_or_update_session(
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices
    )

    if not success:
        raise TooManyRequestsException(message)

    clean_id = id.split('.')[0]

    content_type_map = {"live": "live", "movie": "movie", "series": "series"}
    content_type = content_type_map.get(type, "live")

    original_url = stream_svc.get_original_url(clean_id, content_type)

    if not original_url:
        raise NotFoundException("Stream", id)

    print(f"[DEBUG] original_url: {original_url}")

    use_cache = content_type != "live"
    final_url = await stream_svc.resolve_redirects(original_url, use_cache=use_cache)

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
    stream_svc: StreamProxyService = Depends(get_stream_service)
):
    """Proxy de imágenes/logos para resolver Mixed Content."""
    from urllib.parse import unquote
    import httpx

    try:
        original_url = unquote(url)
    except Exception:
        original_url = url

    if not original_url.startswith('http'):
        original_url = f"http://{original_url}"

    placeholder_map = {
        "movie": "movies.png",
        "series": "series.png",
        "channel": "channels.png",
    }
    placeholder_filename = placeholder_map.get(type, "channels.png")

    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            response = await client.get(original_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            })
            response.raise_for_status()

            content_type = response.headers.get("content-type", "image/jpeg")

            return Response(
                content=response.content,
                media_type=content_type,
                headers={
                    "Cache-Control": "public, max-age=86400",
                }
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
            }
        )

    return Response(
        content=b"",
        status_code=204
    )


# ============================================
# Main
# ============================================

if __name__ == "__main__":
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=3010,
        reload=True
    )