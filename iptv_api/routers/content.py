import logging
import os

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import JSONResponse

from iptv_api.services.channel_favorites_service import ChannelFavoritesServiceV2
from iptv_api.services.content_service import ContentServiceV2
from iptv_api.core.config import get_settings
from iptv_api.core.dependencies import AuthResult as AuthDep
from iptv_api.core.dependencies import (
    get_channel_favorites_service_v2,
    get_content_service_v2,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import BadRequestException, NotFoundException

logger = logging.getLogger("iptv-api")

router = APIRouter()

settings = get_settings()

# Valores reservados que tienen su propio endpoint — nunca deben llegar aquí
_RESERVED_ITEM_IDS = {"full", "all"}

# ============================================
# API: Contenido (Público)
# ============================================


@router.get("/api/content/groups", tags=["Content"])
async def get_groups_public(
    content_type: str = Query("channels", enum=["channels", "movies", "series"]),
    countries: str | None = Query(
        None, description="Filtrar por países (separados por coma: US,MX,ES)"
    ),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    country_list = None
    if countries:
        country_list = [c.strip().upper() for c in countries.split(",") if c.strip()]

    groups = content_svc.get_groups(content_type, country_list)
    if content_type == "channels" and not any(g["value"] == "Favorites" for g in groups):
        groups = [{"value": "Favorites", "label": "Favoritos"}, *groups]
    return {"groups": groups}


@router.get("/api/content/countries", tags=["Content"])
async def get_countries_public(
    content_type: str = Query("channels", enum=["channels", "movies", "series"]),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return {"countries": content_svc.get_countries(content_type)}


@router.get("/api/content", tags=["Content"])
async def get_content(
    content_type: str = Query(
        ..., enum=["channels", "movies", "series"], description="Tipo de contenido"
    ),
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(50, ge=1, le=100, description="Items por página"),
    group: str | None = Query(None, description="Filtrar por grupo"),
    country: str | None = Query(None, description="Filtrar por país"),
    search: str | None = Query(None, description="Buscar por nombre"),
    year: int | None = Query(None, description="Filtrar por año"),
    genre: str | None = Query(None, description="Filtrar por género"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    section_title: str | None = Query(
        None,
        description="Título de sección del home para paginación consistente (ej: 2026 ESTRENOS, NETFLIX)",
    ),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    if content_type == "channels" and group == "Favorites":
        return favorites_svc.get_favorite_channels(
            user_id=auth.user_id,
            content_svc=content_svc,
            page=page,
            page_size=page_size,
            country=country,
            search=search,
            username=auth.username,
            password=password or "",
        )

    if section_title and content_type in ("movies", "series"):
        result = content_svc.get_section_page(
            content_type=content_type,
            section_title=section_title,
            page=page,
            page_size=page_size,
            username=auth.username,
            password=password or "",
            country=country,
            user_id=auth.user_id,
        )
        if result is None:
            raise NotFoundException("Sección", section_title)
        return result

    return content_svc.get_android_content_list(
        content_type=content_type,
        page=page,
        page_size=page_size,
        group=group,
        country=country,
        search=search,
        year=year,
        username=auth.username,
        password=password or "",
        genre=genre,
        user_id=auth.user_id,
    )


@router.get("/api/content/filters", tags=["Content"])
async def get_content_filters(
    content_type: str = Query(
        ..., enum=["channels", "movies", "series"], description="Tipo de contenido"
    ),
    country: str | None = Query(None, description="Filtrar grupos por país"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    payload = content_svc.get_catalog_filters(content_type=content_type, country=country)
    if content_type == "channels" and not any(g["value"] == "Favorites" for g in payload["groups"]):
        payload = {
            **payload,
            "groups": [{"value": "Favorites", "label": "Favoritos"}, *payload["groups"]],
        }
    return payload


@router.get("/api/content/genres", tags=["Content"])
async def get_content_genres(
    content_type: str = Query(..., enum=["movies", "series"], description="Tipo de contenido"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return {"genres": content_svc.get_genres(content_type)}


@router.get("/api/content/stats", tags=["Content"])
async def get_content_stats(
    content_type: str = Query(
        ..., enum=["channels", "movies", "series"], description="Tipo de contenido"
    ),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return content_svc.get_content_stats(content_type=content_type)


# ============================================
# IMPORTANTE: rutas /full y /all ANTES de /{content_type}/{item_id}
# para evitar que el endpoint genérico las capture primero.
# ============================================


@router.get("/api/full/channels", response_class=JSONResponse, tags=["Content"])
async def get_channels_full(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    json_data = content_svc.get_all_content_bulk("channels")
    for base_dir in [
        "/app/data/json",
        os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "walactv-scrapper",
            "data",
            "json",
        ),
    ]:
        gz_path = os.path.join(base_dir, "channels.json.gz")
        if os.path.exists(gz_path):
            with open(gz_path, "rb") as f:
                gz_data = f.read()
            return Response(
                content=gz_data,
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(gz_data)),
                    "X-Content-Type": "channels.json",
                },
            )
    return json_data


@router.get("/api/full/movies", response_class=JSONResponse, tags=["Content"])
async def get_movies_full(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    json_data = content_svc.get_all_content_bulk("movies")
    for base_dir in [
        "/app/data/json",
        os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "walactv-scrapper",
            "data",
            "json",
        ),
    ]:
        gz_path = os.path.join(base_dir, "movies.json.gz")
        if os.path.exists(gz_path):
            with open(gz_path, "rb") as f:
                gz_data = f.read()
            return Response(
                content=gz_data,
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(gz_data)),
                    "X-Content-Type": "movies.json",
                },
            )
    return json_data


@router.get("/api/full/series", response_class=JSONResponse, tags=["Content"])
async def get_series_full(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    json_data = content_svc.get_all_content_bulk("series")
    for base_dir in [
        "/app/data/json",
        os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "..",
            "walactv-scrapper",
            "data",
            "json",
        ),
    ]:
        gz_path = os.path.join(base_dir, "series.json.gz")
        if os.path.exists(gz_path):
            with open(gz_path, "rb") as f:
                gz_data = f.read()
            return Response(
                content=gz_data,
                media_type="application/json",
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(gz_data)),
                    "X-Content-Type": "series.json",
                },
            )
    return json_data


@router.get("/api/full/channels/legacy", tags=["Content"])
async def get_all_channels_bulk(
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    return content_svc.get_all_channels_bulk()


# ============================================
# IMPORTANTE: este endpoint genérico va DESPUÉS de todos los
# endpoints con segmentos literales bajo /api/content/{x}/{y}
# ============================================


@router.get("/api/content/{content_type}/{item_id}", tags=["Content"])
async def get_content_item(
    content_type: str,
    item_id: str,
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    if content_type not in ["channels", "movies", "series"]:
        raise BadRequestException("Tipo de contenido inválido")

    if item_id in _RESERVED_ITEM_IDS:
        raise BadRequestException(
            f"'{item_id}' no es un ID válido. "
            f"Para obtener todo el contenido usa /api/content/{content_type}/{item_id}"
        )

    item = content_svc.get_content_item(
        content_type=content_type,
        item_id=item_id,
        username=auth.username,
        password=password or "",
    )

    if not item:
        content_name = {"channels": "Canal", "movies": "Película", "series": "Serie"}[content_type]
        raise NotFoundException(content_name, item_id)

    return item


@router.get("/api/home", tags=["Content"])
async def get_home(
    page_size: int = Query(24, ge=1, le=50, description="Items por bloque"),
    country: str | None = Query(None, description="Filtrar home por country, por ejemplo ES o EN"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    """Obtiene bloques ligeros para la home de clientes TV."""
    payload = content_svc.get_home_catalog_new(
        username=auth.username,
        country=country,
        password=password or "",
        page_size=page_size,
        user_id=auth.user_id,
    )
    payload["favorites"] = favorites_svc.get_favorite_channels(
        user_id=auth.user_id,
        content_svc=content_svc,
        page=1,
        page_size=page_size,
        country=None,
        search=None,
        username=auth.username,
        password=password or "",
    )["items"]
    logger.info(
        "/api/home: user=%s country=%s favorites=%d",
        auth.username,
        country,
        len(payload["favorites"]),
    )
    return payload


@router.get("/api/home2", tags=["Content"])
async def get_home_v2(
    page_size: int = Query(24, ge=1, le=50, description="Items por bloque"),
    country: str | None = Query(None, description="Filtrar home por country"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    """Obtiene bloques ligeros para la home (nueva versión con paginación infinita)."""
    return content_svc.get_home_catalog_new(
        username=auth.username,
        country=country,
        password=password or "",
        page_size=page_size,
    )


@router.get("/api/content/section", tags=["Content"])
async def get_section(
    content_type: str = Query(..., enum=["movies", "series"], description="Tipo de contenido"),
    section_title: str = Query(..., description="Título de sección: NETFLIX, 2026 ESTRENOS, ..."),
    page: int = Query(
        2,
        ge=1,
        description="Página a cargar (1 = lo que ya tiene /home → empezar en 2)",
    ),
    page_size: int = Query(24, ge=1, le=50, description="Items por página"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    """Carga más items de una sección específica. Para paginación infinita."""
    result = content_svc.get_section_page(
        content_type=content_type,
        section_title=section_title,
        page=page,
        page_size=page_size,
        username=auth.username,
        password=password or "",
        country=None,
    )
    if result is None:
        raise NotFoundException("Sección", section_title)
    return result


@router.get("/api/search", tags=["Content"])
async def search_content(
    q: str = Query(..., min_length=1, description="Texto de búsqueda"),
    types: str | None = Query(
        None, description="Tipos separados por coma: channels,movies,series,events"
    ),
    page: int = Query(1, ge=1, description="Número de página"),
    page_size: int = Query(50, ge=1, le=100, description="Items por página"),
    country: str | None = Query(None, description="Filtrar por país (codigo ISO)"),
    genre: str | None = Query(None, description="Filtrar por género"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    """Busca contenido en varios tipos sin descargar la playlist completa."""
    requested_types = [
        value.strip() for value in (types or "channels,movies,series").split(",") if value.strip()
    ]
    return content_svc.search_catalog(
        query=q,
        types=requested_types,
        page=page,
        page_size=page_size,
        username=auth.username,
        password=password or "",
        country=country,
        genre=genre,
        user_id=auth.user_id,
    )
