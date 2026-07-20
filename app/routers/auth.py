import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt
from starlette.responses import PlainTextResponse

from app.services.device_service import DeviceServiceV2
from app.services.stream_service import StreamProxyServiceV2
from app.services.user_service import UserServiceV2
from utils.config import get_settings
from utils.constants import JWT_ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM
from utils.dependencies import (
    get_device_service_v2,
    get_stream_service_v2,
    get_user_service_v2,
)
from utils.exceptions import (
    ForbiddenException,
    NotFoundException,
    TooManyRequestsException,
    UnauthorizedException,
)
from utils.models import Token

router = APIRouter()

settings = get_settings()
SECRET_KEY = settings.jwt_secret
ALGORITHM = JWT_ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = JWT_ACCESS_TOKEN_EXPIRE_MINUTES


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    """Crea un token JWT de acceso"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


@router.post("/api/auth/login", response_model=Token, tags=["Auth"])
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


@router.get(
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
