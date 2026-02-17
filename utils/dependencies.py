"""
Dependencias reutilizables para la API
"""
from typing import Optional
from fastapi import Depends, Query, Request, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from services import UserService, DeviceService, PlaylistService, StreamProxyService, ContentService, CalendarService
from services.transcode_service import TranscodeService
from utils.config import get_settings
from utils.exceptions import UnauthorizedException, ForbiddenException, ServiceUnavailableException
from utils.models import AuthResult

# Configuración JWT
settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = "HS256"

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# Clientes de servicios (se inicializan en lifespan)
supabase_client = None
user_service: Optional[UserService] = None
device_service: Optional[DeviceService] = None
playlist_service: Optional[PlaylistService] = None
stream_service: Optional[StreamProxyService] = None
content_service: Optional[ContentService] = None
transcode_service: Optional[TranscodeService] = None
calendar_service: Optional[CalendarService] = None


def set_services(
    user_svc: UserService,
    device_svc: DeviceService,
    playlist_svc: PlaylistService,
    stream_svc: StreamProxyService,
    content_svc: ContentService,
    transcode_svc: TranscodeService,
    calendar_svc: CalendarService
):
    """Inicializa los servicios globales"""
    global user_service, device_service, playlist_service, stream_service, content_service, transcode_service, calendar_service
    user_service = user_svc
    device_service = device_svc
    playlist_service = playlist_svc
    stream_service = stream_svc
    content_service = content_svc
    transcode_service = transcode_svc
    calendar_service = calendar_svc


# ============================================
# Dependencias de Servicios
# ============================================

def get_user_service() -> UserService:
    if not user_service:
        raise ServiceUnavailableException("Servicio de usuarios no disponible")
    return user_service


def get_device_service() -> DeviceService:
    if not device_service:
        raise ServiceUnavailableException("Servicio de dispositivos no disponible")
    return device_service


def get_playlist_service() -> PlaylistService:
    if not playlist_service:
        raise ServiceUnavailableException("Servicio de playlists no disponible")
    return playlist_service


def get_stream_service() -> StreamProxyService:
    if not stream_service:
        raise ServiceUnavailableException("Servicio de streaming no disponible")
    return stream_service


def get_content_service() -> ContentService:
    if not content_service:
        raise ServiceUnavailableException("Servicio de contenido no disponible")
    return content_service


def get_transcode_service() -> TranscodeService:
    if not transcode_service:
        raise ServiceUnavailableException("Servicio de transcodificación no disponible")
    return transcode_service


def get_calendar_service() -> CalendarService:
    if not calendar_service:
        raise ServiceUnavailableException("Servicio de calendario no disponible")
    return calendar_service


# ============================================
# Dependencias de Autenticación
# ============================================

async def get_current_user(token: str = Depends(oauth2_scheme)) -> dict:
    """
    Verifica el token JWT y retorna el usuario.
    Lanza UnauthorizedException si el token es inválido.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        role: str = payload.get("role")

        if user_id is None or role is None:
            raise UnauthorizedException("Token inválido: falta sub o role")

        return {"id": user_id, "role": role}

    except JWTError:
        raise UnauthorizedException("Token inválido o expirado")


async def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """
    Verifica que el usuario tenga rol de admin.
    Lanza ForbiddenException si no es admin.
    """
    if current_user.get("role") != "admin":
        raise ForbiddenException("Se requieren permisos de administrador")
    return current_user


async def require_auth_with_jwt(
    token: str = Depends(oauth2_scheme),
    user_svc: UserService = Depends(get_user_service)
) -> AuthResult:
    """
    Valida token JWT Bearer y retorna AuthResult.
    Usado para endpoints de contenido que requieren autenticación.
    """
    global user_service
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        
        if user_id is None:
            raise UnauthorizedException("Token inválido")
        
        # Obtener usuario para verificar estado
        user = user_svc.get_user(user_id)
        
        if user is None:
            raise UnauthorizedException("Usuario no encontrado")
        
        if not user.get("is_active", True):
            raise ForbiddenException("Usuario desactivado")
        
        # Retornar AuthResult con todos los campos requeridos
        return AuthResult(
            valid=True,
            user_id=user_id,
            username=user.get("username"),
            message="OK",
            can_connect=True,
            current_devices=user.get("active_devices", 0),
            max_devices=user.get("max_connections", 5)
        )
        
    except JWTError:
        raise UnauthorizedException("Token inválido o expirado")


async def require_auth_with_credentials(
    username: str = Query(..., description="Nombre de usuario"),
    password: str = Query(..., description="Contraseña"),
    user_svc: UserService = Depends(get_user_service)
) -> AuthResult:
    """
    Valida credenciales desde query parameters.
    Usado para endpoints públicos que requieren autenticación.
    """
    auth = user_svc.validate_credentials(username, password)
    
    if not auth.valid:
        raise UnauthorizedException(auth.message)
    
    return AuthResult(
        valid=auth.valid,
        user_id=auth.user_id,
        username=auth.username,
        message=auth.message,
        can_connect=auth.can_connect,
        current_devices=auth.current_devices,
        max_devices=auth.max_devices
    )


async def require_auth_with_session(
    request: Request,
    username: str = Query(..., description="Nombre de usuario"),
    password: str = Query(..., description="Contraseña"),
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service)
) -> AuthResult:
    """
    Valida credenciales y registra/actualiza la sesión del dispositivo.
    Usado para endpoints de streaming que requieren control de dispositivos.
    """
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
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=message
        )
    
    return AuthResult(
        valid=auth.valid,
        user_id=auth.user_id,
        username=auth.username,
        message=auth.message,
        can_connect=auth.can_connect,
        current_devices=auth.current_devices,
        max_devices=auth.max_devices
    )
