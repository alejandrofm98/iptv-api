import asyncio
import gzip
import logging

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from starlette.responses import RedirectResponse

from iptv_api.services.device_service import DeviceServiceV2
from iptv_api.services.playlist_service import PlaylistServiceV2
from iptv_api.services.stream_service import StreamProxyServiceV2
from iptv_api.services.transcode_service import TranscodeService
from iptv_api.services.user_service import UserServiceV2
from iptv_api.core.dependencies import (
    get_device_service_v2,
    get_playlist_service_v2,
    get_stream_service_v2,
    get_transcode_service,
    get_user_service_v2,
)
from iptv_api.core.exceptions import (
    BadRequestException,
    ForbiddenException,
    NotFoundException,
    TooManyRequestsException,
    UnauthorizedException,
)

logger = logging.getLogger("iptv-api")

router = APIRouter()

ALLOWED_WEB_ORIGINS = ["https://walactvweb.walerike.com", "http://localhost:4200"]


# ============================================
# Funciones auxiliares internas
# ============================================


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

    from iptv_api.core.config import get_settings

    settings = get_settings()

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


# ============================================
# Stream Proxy Handler
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


@router.get("/{content_type}/{username}/{password}/{stream_id}", tags=["Stream"])
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


@router.get("/{username}/{password}/{stream_id}", tags=["Stream"])
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


# ============================================
# Playlist M3U
# ============================================


@router.get("/get.php", tags=["Playlist"])
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


@router.get("/hls/{session_id}/playlist.m3u8", tags=["HLS"])
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


@router.get("/hls/{session_id}/{segment}", tags=["HLS"])
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


# ============================================
# Chromecast endpoints
# ============================================


@router.get("/cast/hls/{session_id}/{segment}", tags=["HLS", "Chromecast"])
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


@router.options("/cast/hls/{session_id}/{segment}", tags=["HLS", "Chromecast"])
async def cast_hls_segment_options(session_id: str, segment: str, request: Request):
    return _build_cast_options_response(request)


@router.get(
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


@router.options(
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


@router.get(
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


@router.options(
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
