"""
Dependencias reutilizables para la API
"""
from typing import Optional
from fastapi import Depends, Query, Request, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt

from services import UserService, DeviceService, PlaylistService, StreamProxyService, ContentService
from utils.config import get_settings
from utils.exceptions import UnauthorizedException, ForbiddenException, ServiceUnavailableException

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


def set_services(
    user_svc: UserService,
    device_svc: DeviceService,
    playlist_svc: PlaylistService,
    stream_svc: StreamProxyService,
    content_svc: ContentService
):
    """Inicializa los servicios globales"""
    global user_service, device_service, playlist_service, stream_service, content_service
    user_service = user_svc
    device_service = device_svc
    playlist_service = playlist_svc
    stream_service = stream_svc
    content_service = content_svc


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


class AuthResult:
    """Resultado de autenticación con user/pass"""
    def __init__(self, auth_data):
        self.valid = auth_data.valid
        self.user_id = auth_data.user_id
        self.username = auth_data.username
        self.can_connect = auth_data.can_connect
        self.max_devices = auth_data.max_devices
        self.message = auth_data.message


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
    
    return AuthResult(auth)


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
    
    return AuthResult(auth)
