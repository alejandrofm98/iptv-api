import asyncio
import logging
from urllib.parse import quote, urljoin

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt

from app.services.content_service import ContentServiceV2
from utils.config import get_settings
from utils.dependencies import AuthResult as AuthDep
from utils.dependencies import (
    get_content_service_v2,
    require_auth_with_jwt,
)
from utils.exceptions import NotFoundException, UnauthorizedException

logger = logging.getLogger("iptv-api")

router = APIRouter()

settings = get_settings()


def validate_stream_token(token: str) -> dict:
    """Valida un JWT para uso en proxy de streaming."""
    ALGORITHM = "HS256"
    SECRET_KEY = settings.jwt_secret
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
# API: Replays
# ============================================


@router.get("/api/replays", tags=["Replays"])
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


@router.get("/api/replays/{slug}", tags=["Replays"])
async def get_replay(
    slug: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    item = content_svc.get_replay(slug)
    if not item:
        raise NotFoundException("Replay", slug)
    return item


@router.get("/api/replay-proxy", tags=["Replays"])
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


@router.get("/api/replays/{slug}/stream/{source_index}/{button_index}", tags=["Replays"])
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
