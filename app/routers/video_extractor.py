import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.services.device_service import DeviceServiceV2
from app.services.stream_service import StreamProxyServiceV2
from app.services.user_service import UserServiceV2
from services.video_extractor_service import VideoExtractorService
from utils.dependencies import AuthResult as AuthDep
from utils.dependencies import (
    get_device_service_v2,
    get_stream_service_v2,
    get_user_service_v2,
    require_auth_with_jwt,
)
from utils.exceptions import (
    BadRequestException,
    NotFoundException,
    TooManyRequestsException,
    UnauthorizedException,
)

logger = logging.getLogger("iptv-api")

router = APIRouter()


# ─── modelos Pydantic ────────────────────────────────────────────────────────
class ExtractRequest(BaseModel):
    url: str
    """URL de embed del proveedor (streamtape, netu, streamwish, etc.)"""


class ExtractMultiRequest(BaseModel):
    urls: list[str]
    """Lista de URLs a extraer en paralelo (máximo 10)."""


def get_video_extractor() -> VideoExtractorService:
    """Dependencia reutilizable — instancia sin estado, segura para concurrencia."""
    return VideoExtractorService()


# ============================================
# API: Video Extract
# ============================================


@router.get("/api/video-extract", tags=["Video Extractor"])
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


@router.post("/api/video-extract/multi", tags=["Video Extractor"])
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
# API: Internal Stream URL (for Nginx direct proxy)
# ============================================


@router.get("/internal/stream-url", tags=["Internal"])
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
    import asyncio

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
