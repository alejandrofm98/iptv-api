from pathlib import Path
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Depends, Query, Response

from app.services.stream_service import StreamProxyServiceV2
from utils.dependencies import get_stream_service_v2

router = APIRouter()

IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "resources" / "images"


@router.get("/logo", tags=["Logo"])
async def proxy_logo(
    url: str = Query(..., description="URL original del logo"),
    type: str = Query("channel", description="Tipo: channel, movie, series"),
    stream_svc: StreamProxyServiceV2 = Depends(get_stream_service_v2),
):
    """Proxy de imágenes/logos para resolver Mixed Content."""
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

    placeholder_path = IMAGES_DIR / placeholder_filename
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
