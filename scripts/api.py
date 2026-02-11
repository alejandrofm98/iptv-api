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
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Query, Depends, Header, status, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt

from services import UserService, DeviceService, PlaylistService, StreamProxyService, ContentService
from utils.config import get_settings
from utils.models import UserCreate, UserUpdate, ValidateCredentials, AuthResult, SystemStats, Token
from utils.constants import JWT_ALGORITHM, JWT_ACCESS_TOKEN_EXPIRE_MINUTES
from utils.exceptions import (
    NotFoundException, UnauthorizedException, ForbiddenException,
    BadRequestException, ConflictException, TooManyRequestsException
)
from utils.dependencies import (
    set_services, get_user_service, get_device_service, get_playlist_service,
    get_stream_service, get_content_service, get_current_user, require_admin,
    require_auth_with_credentials, require_auth_with_session, require_auth_with_jwt,
    AuthResult as AuthDep
)

# Configuración
settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = JWT_ACCESS_TOKEN_EXPIRE_MINUTES

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


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

        # Inicializar servicios globales
        set_services(user_svc, device_svc, playlist_svc, stream_svc, content_svc)

        stream_svc.preload_cache()
        asyncio.create_task(cleanup_sessions_task())
        print("✅ IPTV API iniciada correctamente")

    yield

    print("🛑 Cerrando IPTV API...")


# ============================================
# Crear aplicación
# ============================================

app = FastAPI(
    title="IPTV API",
    description="API para gestión de usuarios IPTV con control de dispositivos y JWT",
    version="2.1.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://walactvweb.walerike.com",
        "http://localhost:4200",
        "*"  # Temporal para debugging
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

# Manejador de excepciones global para asegurar CORS
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

    # Obtener total para paginación
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
    # Paginar manualmente
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


# ============================================
# API: Contenido (Público)
# ============================================

@app.get("/api/content/groups", tags=["Content"])
async def get_groups_public(
    content_type: str = Query('channels', enum=['channels', 'movies', 'series']),
    countries: Optional[str] = Query(None, description="Filtrar por países (separados por coma: US,MX,ES)"),
    auth: AuthResult = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """
    Obtiene grupos disponibles por tipo de contenido.
    Opcionalmente filtra por uno o varios países.
    Requiere Bearer Token.
    """
    country_list = None
    if countries:
        country_list = [c.strip().upper() for c in countries.split(',') if c.strip()]
    
    return {"groups": content_svc.get_groups(content_type, country_list)}


@app.get("/api/content/countries", tags=["Content"])
async def get_countries_public(
    content_type: str = Query('channels', enum=['channels', 'movies', 'series']),
    auth: AuthResult = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """Obtiene países disponibles por tipo de contenido (requiere Bearer Token)"""
    return {"countries": content_svc.get_countries(content_type)}

@app.post("/api/admin/content/reload", tags=["Admin - Content"])
async def reload_template(
    _: dict = Depends(require_admin),
    playlist_svc: PlaylistService = Depends(get_playlist_service)
):
    """Recarga el template M3U en memoria"""
    playlist_svc.reload_template()
    if playlist_svc._template_cache is not None:
        return {
            "status": "success",
            "message": "Template recargado correctamente",
            "size": len(playlist_svc._template_cache)
        }
    else:
        raise BadRequestException("No se pudo recargar el template. Verifica que playlist_template.m3u exista.")


# ============================================
# API: Contenido (Público - Requiere Auth)
# =========================================###

@app.get("/api/content", tags=["Content"])
async def get_content(
    content_type: str = Query(..., enum=['channels', 'movies', 'series'], description="Tipo de contenido"),
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(50, ge=1, le=100, description="Items por página"),
    group: Optional[str] = Query(None, description="Filtrar por grupo"),
    country: Optional[str] = Query(None, description="Filtrar por país"),
    search: Optional[str] = Query(None, description="Buscar por nombre"),
    auth: AuthResult = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """
    Obtiene lista paginada de contenido (canales, películas o series).
    Requiere Bearer Token.
    """
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
    auth: AuthResult = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """
    Obtiene un item específico de contenido.
    Requiere Bearer Token.
    """
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
    auth: AuthResult = Depends(require_auth_with_jwt),
    content_svc: ContentService = Depends(get_content_service)
):
    """
    Obtiene el total de canales, películas y series disponibles.
    Requiere Bearer Token.
    """
    counts = content_svc.get_content_count()
    return {
        "channels": counts['channels'],
        "movies": counts['movies'],
        "series": counts['series'],
        "total": counts['channels'] + counts['movies'] + counts['series']
    }


# ============================================
# API: Playlist M3U
# ============================================

@app.get("/playlist/{username}/{password}.m3u", tags=["Playlist"])
async def get_playlist(
    username: str,
    password: str,
    request: Request,
    content_type: Optional[str] = Query(None, enum=['channels', 'movies', 'series'], description="Tipo de contenido (omitir para todos)"),
    group: Optional[str] = Query(None, description="Filtrar por grupo"),
    country: Optional[str] = Query(None, description="Filtrar por país"),
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    playlist_svc: PlaylistService = Depends(get_playlist_service)
):
    """Genera playlist M3U para el usuario autenticado"""
    # Validar credenciales desde path params
    auth = user_svc.validate_credentials(username, password)
    
    if not auth.valid:
        raise UnauthorizedException(auth.message)
    
    if not auth.can_connect:
        raise ForbiddenException(auth.message)
    
    # Registrar sesión del dispositivo
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
    
    include_channels = content_type is None or content_type == 'channels'
    include_movies = content_type is None or content_type == 'movies'
    include_series = content_type is None or content_type == 'series'

    m3u_content = playlist_svc.generate_m3u(
        username=username,
        password=password,
        include_channels=include_channels,
        include_movies=include_movies,
        include_series=include_series,
        group_filter=group,
        country_filter=country
    )

    return PlainTextResponse(
        content=m3u_content,
        media_type="application/vnd.apple.mpegurl; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{username}_playlist.m3u"',
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
    )


# ============================================
# API: Stream Proxy
# ============================================

@app.get("/stream/{content_type}/{username}/{password}/{stream_id}", tags=["Stream"])
async def proxy_stream(
    content_type: str,
    username: str,
    password: str,
    stream_id: str,
    request: Request,
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    stream_svc: StreamProxyService = Depends(get_stream_service)
):
    """
    Proxy de streams para canales, películas y series.
    Endpoint unificado que reemplaza /live/, /movie/, /series/.
    """
    if content_type not in ['live', 'movie', 'series']:
        raise BadRequestException("Tipo de stream inválido", {"valid_types": ["live", "movie", "series"]})
    
    # Validar credenciales desde path params
    auth = user_svc.validate_credentials(username, password)
    
    if not auth.valid:
        raise UnauthorizedException(auth.message)
    
    if not auth.can_connect:
        raise ForbiddenException(auth.message)
    
    # Registrar sesión del dispositivo
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

    if not original_url:
        raise NotFoundException("Stream", stream_id)

    try:
        status_code, headers, body = await stream_svc.get_stream_response(original_url)

        return StreamingResponse(
            body,
            status_code=status_code,
            headers=headers,
            media_type=headers.get('content-type', 'video/mp2t')
        )
    except Exception as e:
        raise BadRequestException(f"Error al obtener stream: {str(e)}")


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
    """
    Valida credenciales y devuelve URL original para nginx.
    Usado por nginx auth_request para validar antes de proxy directo.
    """
    # Validar credenciales desde path params
    auth = user_svc.validate_credentials(username, password)
    
    if not auth.valid:
        raise UnauthorizedException(auth.message)
    
    if not auth.can_connect:
        raise ForbiddenException(auth.message)
    
    # Registrar sesión del dispositivo
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
# API: Logo/Image Proxy
# ============================================

@app.get("/logo", tags=["Logo"])
async def proxy_logo(
    url: str = Query(..., description="URL original del logo"),
    stream_svc: StreamProxyService = Depends(get_stream_service)
):
    """
    Proxy de imágenes/logos para resolver Mixed Content.
    Recibe una URL HTTP del proveedor y la sirve a través de HTTPS.
    """
    from urllib.parse import unquote
    import httpx

    original_url = unquote(url)

    if not original_url.startswith('http'):
        raise BadRequestException("URL inválida: falta protocolo http:// o https://")

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
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
                    "Access-Control-Allow-Origin": "*"
                }
            )
    except httpx.HTTPStatusError as e:
        raise BadRequestException(f"Error al obtener imagen: HTTP {e.response.status_code}")
    except httpx.RequestError as e:
        raise BadRequestException(f"Error al obtener imagen: {str(e)}")


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
