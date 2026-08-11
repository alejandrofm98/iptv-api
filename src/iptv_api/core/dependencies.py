"""
Dependencias reutilizables para la API
"""

import logging

from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

logger = logging.getLogger("iptv-api")

# Imports de app/services deben ir después del logger para configuración temprana
from iptv_api.core.config import get_settings  # noqa: E402
from iptv_api.core.exceptions import (  # noqa: E402
    ForbiddenException,
    ServiceUnavailableException,
    UnauthorizedException,
)
from iptv_api.core.models import AuthResult  # noqa: E402
from iptv_api.database import get_session  # noqa: E402
from iptv_api.services.calendar_service import CalendarServiceV2  # noqa: E402
from iptv_api.services.channel_favorites_service import ChannelFavoritesServiceV2  # noqa: E402
from iptv_api.services.content_service import ContentServiceV2  # noqa: E402
from iptv_api.services.device_service import DeviceServiceV2  # noqa: E402
from iptv_api.services.playback_preference_service import PlaybackPreferenceService  # noqa: E402
from iptv_api.services.playlist_service import PlaylistServiceV2  # noqa: E402
from iptv_api.services.stream_service import StreamProxyServiceV2  # noqa: E402
from iptv_api.services.transcode_service import TranscodeService  # noqa: E402
from iptv_api.services.user_service import UserServiceV2  # noqa: E402
from iptv_api.services.watch_progress_service import WatchProgressServiceV2  # noqa: E402

# Configuración JWT
settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = "HS256"

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# Cliente singleton para transcode (no usa BD)
transcode_service: TranscodeService | None = None


# ============================================
# Dependencias de Servicios
# ============================================


def get_transcode_service() -> TranscodeService:
    if not transcode_service:
        raise ServiceUnavailableException("Servicio de transcodificación no disponible")
    return transcode_service


# ============================================
# Dependencias SQLAlchemy v2
# ============================================


def get_db():
    """Crea una sesión de SQLAlchemy por request."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_watch_progress_service_v2(
    session: Session = Depends(get_db),
) -> WatchProgressServiceV2:
    return WatchProgressServiceV2(session)


def get_playback_preference_service(
    session: Session = Depends(get_db),
) -> PlaybackPreferenceService:
    return PlaybackPreferenceService(session)


def get_user_service_v2(
    session: Session = Depends(get_db),
) -> UserServiceV2:
    return UserServiceV2(session)


def get_device_service_v2(
    session: Session = Depends(get_db),
) -> DeviceServiceV2:
    return DeviceServiceV2(session)


def get_channel_favorites_service_v2(
    session: Session = Depends(get_db),
) -> ChannelFavoritesServiceV2:
    return ChannelFavoritesServiceV2(session)


def get_calendar_service_v2(
    session: Session = Depends(get_db),
) -> CalendarServiceV2:
    return CalendarServiceV2(session)


_playlist_service_v2: PlaylistServiceV2 | None = None


def get_stream_service_v2(
    session: Session = Depends(get_db),
) -> StreamProxyServiceV2:
    from iptv_api.repositories.channel_repo import ChannelRepository
    from iptv_api.repositories.config_repo import ConfigRepository
    from iptv_api.repositories.content_repo import ContentRepository
    from iptv_api.repositories.series_repo import SeriesRepository

    return StreamProxyServiceV2(
        config_repo=ConfigRepository(session),
        channel_repo=ChannelRepository(session),
        content_repo=ContentRepository(session),
        series_repo=SeriesRepository(session),
    )


def get_playlist_service_v2() -> PlaylistServiceV2:
    global _playlist_service_v2
    if _playlist_service_v2 is None:
        _playlist_service_v2 = PlaylistServiceV2()
    return _playlist_service_v2


def get_content_service_v2(
    session: Session = Depends(get_db),
) -> ContentServiceV2:
    return ContentServiceV2(session)


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
        raise UnauthorizedException("Token inválido o expirado") from None


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
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
) -> AuthResult:
    """
    Valida token JWT Bearer y retorna AuthResult.
    Usado para endpoints de contenido que requieren autenticación.
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")

        if user_id is None:
            logger.warning("[DIAG] JWT auth: token has no 'sub' claim")
            raise UnauthorizedException("Token inválido")

        user = user_svc.get_user(user_id)
        logger.info(f"[DIAG] JWT auth: user_id={user_id}, user_found={user is not None}")

        if user is None:
            raise UnauthorizedException("Usuario no encontrado")

        if not user.get("is_active", True):
            raise ForbiddenException("Usuario desactivado")

        return AuthResult(
            valid=True,
            user_id=user_id,
            username=user.get("username"),
            message="OK",
            can_connect=True,
            current_devices=user.get("active_devices", 0),
            max_devices=user.get("max_connections", 5),
        )

    except JWTError:
        raise UnauthorizedException("Token inválido o expirado") from None


async def require_auth_with_credentials(
    username: str = Query(..., description="Nombre de usuario"),
    password: str = Query(..., description="Contraseña"),
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
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
        max_devices=auth.max_devices,
    )


async def require_auth_with_session(
    request: Request,
    username: str = Query(..., description="Nombre de usuario"),
    password: str = Query(..., description="Contraseña"),
    user_svc: UserServiceV2 = Depends(get_user_service_v2),
    device_svc: DeviceServiceV2 = Depends(get_device_service_v2),
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

    user_agent = request.headers.get("User-Agent", "Unknown")
    ip_address = request.client.host if request.client else "Unknown"

    success, message, _ = device_svc.register_or_update_session(
        user_id=auth.user_id,
        user_agent=user_agent,
        ip_address=ip_address,
        max_connections=auth.max_devices,
    )

    if not success:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=message)

    return AuthResult(
        valid=auth.valid,
        user_id=auth.user_id,
        username=auth.username,
        message=auth.message,
        can_connect=auth.can_connect,
        current_devices=auth.current_devices,
        max_devices=auth.max_devices,
    )
