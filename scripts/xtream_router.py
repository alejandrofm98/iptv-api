"""
Xtream Codes API - Compatibilidad con reproductores IPTV

Implementa /player_api.php con las acciones estándar:
- Sin action / action=get_live_categories / get_vod_categories / get_series_categories
- action=get_live_streams / get_vod_streams / get_series
- action=get_series_info (episodios de una serie)
- action=get_vod_info (info de una película)

Formato de respuesta compatible con Xtream Codes Panel.
"""
import logging
from typing import Optional
from fastapi import APIRouter, Query, Request, Depends
from fastapi.responses import JSONResponse

from services import UserService, DeviceService, ContentService
from utils.config import get_settings
from utils.dependencies import get_user_service, get_device_service, get_content_service
from utils.exceptions import UnauthorizedException, ForbiddenException, TooManyRequestsException

logger = logging.getLogger("iptv-api")
router = APIRouter()
settings = get_settings()


def _build_stream_url(content_type: str, username: str, password: str, provider_id: str) -> str:
    """Construye la URL de stream en formato Xtream Codes"""
    domain = settings.public_domain.rstrip('/')
    if content_type == 'live':
        return f"{domain}/live/{username}/{password}/{provider_id}"
    elif content_type == 'movie':
        return f"{domain}/movie/{username}/{password}/{provider_id}"
    else:
        return f"{domain}/series/{username}/{password}/{provider_id}"


def _server_info(username: str, auth) -> dict:
    """Devuelve el bloque server_info que esperan los reproductores Xtream"""
    domain = settings.public_domain.rstrip('/')
    # Extraer host y puerto del dominio
    host = domain.replace('https://', '').replace('http://', '')
    port = "443" if domain.startswith('https') else "80"
    protocol = "https" if domain.startswith('https') else "http"

    return {
        "url": host,
        "port": port,
        "https_port": "443",
        "server_protocol": protocol,
        "rtmp_port": "1935",
        "timezone": "Europe/Madrid",
        "timestamp_now": int(__import__('time').time()),
        "time_now": __import__('datetime').datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "process": True
    }


def _user_info(username: str, auth) -> dict:
    """Devuelve el bloque user_info que esperan los reproductores Xtream"""
    import time
    # Fecha de expiración: 1 año desde ahora si no hay dato
    exp_date = str(int(time.time()) + 365 * 24 * 3600)

    return {
        "username": username,
        "password": "",  # No exponer contraseña real
        "message": "",
        "auth": 1,
        "status": "Active",
        "exp_date": exp_date,
        "is_trial": "0",
        "active_cons": "1",
        "created_at": str(int(time.time()) - 30 * 24 * 3600),
        "max_connections": str(getattr(auth, 'max_devices', 1)),
        "allowed_output_formats": ["ts", "m3u8", "rtmp"]
    }


async def _validate(
    username: str,
    password: str,
    request: Request,
    user_svc: UserService,
    device_svc: DeviceService
):
    """Valida credenciales y registra sesión. Lanza excepción si falla."""
    auth = user_svc.validate_credentials(username, password)

    if not auth.valid:
        raise UnauthorizedException(auth.message)

    if not auth.can_connect:
        raise ForbiddenException(auth.message)

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

    return auth


# ============================================
# /player_api.php — Endpoint principal Xtream
# ============================================

@router.get("/player_api.php")
async def player_api(
    request: Request,
    username: str = Query(...),
    password: str = Query(...),
    action: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    vod_id: Optional[str] = Query(None),
    series_id: Optional[str] = Query(None),
    stream_id: Optional[str] = Query(None),
    user_svc: UserService = Depends(get_user_service),
    device_svc: DeviceService = Depends(get_device_service),
    content_svc: ContentService = Depends(get_content_service),
):
    """
    Endpoint de compatibilidad Xtream Codes.
    Los reproductores IPTV llaman aquí para obtener categorías,
    listas de canales, películas y series.
    """
    # Validar credenciales
    try:
        auth = await _validate(username, password, request, user_svc, device_svc)
    except (UnauthorizedException, ForbiddenException):
        return JSONResponse(content={"user_info": {"auth": 0}, "server_info": {}})
    except TooManyRequestsException as e:
        return JSONResponse(content={"user_info": {"auth": 0, "message": str(e)}, "server_info": {}})

    server = _server_info(username, auth)
    user = _user_info(username, auth)

    logger.info(f"🎮 Xtream API: user={username}, action={action}, cat={category_id}")

    # ── Sin action: devolver info del servidor (llamada de login) ──
    if not action:
        return JSONResponse(content={
            "user_info": user,
            "server_info": server
        })

    # ── Categorías de canales en vivo ──
    if action == "get_live_categories":
        groups = content_svc.get_groups('channels')
        categories = []
        for i, group in enumerate(groups, start=1):
            categories.append({
                "category_id": str(i),
                "category_name": group,
                "parent_id": 0
            })
        return JSONResponse(content=categories)

    # ── Categorías de películas ──
    if action == "get_vod_categories":
        groups = content_svc.get_groups('movies')
        categories = []
        for i, group in enumerate(groups, start=1):
            categories.append({
                "category_id": str(i),
                "category_name": group,
                "parent_id": 0
            })
        return JSONResponse(content=categories)

    # ── Categorías de series ──
    if action == "get_series_categories":
        groups = content_svc.get_groups('series')
        categories = []
        for i, group in enumerate(groups, start=1):
            categories.append({
                "category_id": str(i),
                "category_name": group,
                "parent_id": 0
            })
        return JSONResponse(content=categories)

    # ── Canales en vivo ──
    if action == "get_live_streams":
        group_name = _resolve_category(content_svc, 'channels', category_id)
        streams = _get_all_content(content_svc, 'channels', group_name)
        result = []
        for item in streams:
            provider_id = item.get('provider_id') or item.get('id', '')
            result.append({
                "num": item.get('numero', 0),
                "name": item.get('nombre', ''),
                "stream_type": "live",
                "stream_id": int(provider_id) if str(provider_id).isdigit() else 0,
                "stream_icon": item.get('logo', ''),
                "epg_channel_id": item.get('tvg_id', ''),
                "added": "0",
                "category_id": category_id or "1",
                "custom_sid": "",
                "tv_archive": 0,
                "direct_source": "",
                "tv_archive_duration": 0
            })
        return JSONResponse(content=result)

    # ── Películas (VOD) ──
    if action == "get_vod_streams":
        group_name = _resolve_category(content_svc, 'movies', category_id)
        streams = _get_all_content(content_svc, 'movies', group_name)
        result = []
        for item in streams:
            provider_id = item.get('provider_id') or item.get('id', '')
            result.append({
                "num": item.get('numero', 0),
                "name": item.get('nombre', ''),
                "stream_type": "movie",
                "stream_id": int(provider_id) if str(provider_id).isdigit() else 0,
                "stream_icon": item.get('logo', ''),
                "rating": "",
                "rating_5based": 0,
                "added": "0",
                "category_id": category_id or "1",
                "container_extension": "ts",
                "custom_sid": "",
                "direct_source": ""
            })
        return JSONResponse(content=result)

    # ── Series ──
    if action == "get_series":
        group_name = _resolve_category(content_svc, 'series', category_id)
        streams = _get_all_content(content_svc, 'series', group_name)

        # Agrupar episodios por serie_name
        series_map = {}
        for item in streams:
            serie_name = item.get('serie_name') or item.get('nombre', '')
            if serie_name not in series_map:
                provider_id = item.get('provider_id') or item.get('id', '')
                series_map[serie_name] = {
                    "series_id": abs(hash(serie_name)) % 1000000,
                    "name": serie_name,
                    "cover": item.get('logo', ''),
                    "plot": "",
                    "cast": "",
                    "director": "",
                    "genre": item.get('grupo', ''),
                    "release_date": "",
                    "last_modified": "0",
                    "rating": "",
                    "rating_5based": 0,
                    "backdrop_path": [],
                    "youtube_trailer": "",
                    "episode_run_time": "",
                    "category_id": category_id or "1"
                }
        return JSONResponse(content=list(series_map.values()))

    # ── Info detallada de una serie (episodios) ──
    if action == "get_series_info" and series_id:
        streams = _get_all_content(content_svc, 'series', None)

        # Buscar episodios que coincidan con el series_id (hash del nombre)
        target_id = int(series_id)
        episodes_by_season = {}
        serie_name = None
        serie_logo = None

        for item in streams:
            name = item.get('serie_name') or item.get('nombre', '')
            item_id = abs(hash(name)) % 1000000
            if item_id == target_id:
                serie_name = name
                serie_logo = item.get('logo', '')
                season = item.get('temporada', '1') or '1'
                episode = item.get('episodio', '1') or '1'
                provider_id = item.get('provider_id') or item.get('id', '')

                if season not in episodes_by_season:
                    episodes_by_season[season] = {}

                episodes_by_season[season][episode] = {
                    "id": str(provider_id),
                    "episode_num": int(episode) if str(episode).isdigit() else 1,
                    "title": item.get('nombre', ''),
                    "container_extension": "ts",
                    "info": {
                        "movie_image": item.get('logo', ''),
                        "plot": "",
                        "releasedate": "",
                        "rating": "",
                        "duration_secs": 0,
                        "duration": ""
                    },
                    "subtitles": [],
                    "added": "0",
                    "season": int(season) if str(season).isdigit() else 1,
                    "direct_source": ""
                }

        return JSONResponse(content={
            "seasons": [
                {"air_date": "", "episode_count": len(eps), "id": int(s) if s.isdigit() else 1,
                 "name": f"Season {s}", "overview": "", "season_number": int(s) if s.isdigit() else 1}
                for s, eps in episodes_by_season.items()
            ],
            "info": {
                "name": serie_name or "",
                "cover": serie_logo or "",
                "plot": "",
                "cast": "",
                "director": "",
                "genre": "",
                "release_date": "",
                "last_modified": "0",
                "rating": "",
                "rating_5based": 0,
                "backdrop_path": [],
                "youtube_trailer": "",
                "episode_run_time": "",
                "category_id": "1"
            },
            "episodes": episodes_by_season
        })

    # ── Info detallada de una película ──
    if action == "get_vod_info" and vod_id:
        # Buscar la película por provider_id
        streams = _get_all_content(content_svc, 'movies', None)
        movie = next((m for m in streams if str(m.get('provider_id', '')) == str(vod_id)
                      or str(m.get('id', '')) == str(vod_id)), None)

        if not movie:
            return JSONResponse(content={"info": {}, "movie_data": {}})

        provider_id = movie.get('provider_id') or movie.get('id', '')
        return JSONResponse(content={
            "info": {
                "kinopoisk_url": "",
                "tmdb_id": "",
                "name": movie.get('nombre', ''),
                "o_name": movie.get('nombre', ''),
                "cover_big": movie.get('logo', ''),
                "movie_image": movie.get('logo', ''),
                "release_date": "",
                "episode_run_time": "",
                "youtube_trailer": "",
                "director": "",
                "actors": "",
                "cast": "",
                "description": "",
                "plot": "",
                "age": "",
                "mpaa_rating": "",
                "rating_count_kinopoisk": 0,
                "country": movie.get('country', ''),
                "genre": movie.get('grupo', ''),
                "backdrop_path": [],
                "duration_secs": 0,
                "duration": "",
                "video": {},
                "audio": {},
                "bitrate": 0,
                "rating": "",
                "rating_5based": 0,
                "added": "0"
            },
            "movie_data": {
                "stream_id": int(provider_id) if str(provider_id).isdigit() else 0,
                "name": movie.get('nombre', ''),
                "added": "0",
                "category_id": "1",
                "container_extension": "ts",
                "custom_sid": "",
                "direct_source": ""
            }
        })

    # Acción no reconocida
    return JSONResponse(content=[])


# ============================================
# Helpers internos
# ============================================

def _resolve_category(content_svc: ContentService, content_type: str, category_id: Optional[str]) -> Optional[str]:
    """
    Convierte un category_id numérico al nombre real del grupo.
    Los IDs son posicionales (1 = primer grupo, 2 = segundo...).
    """
    if not category_id:
        return None
    try:
        idx = int(category_id) - 1
        groups = content_svc.get_groups(content_type)
        if 0 <= idx < len(groups):
            return groups[idx]
    except (ValueError, IndexError):
        pass
    return None


def _get_all_content(content_svc: ContentService, content_type: str, group_name: Optional[str]) -> list:
    """
    Obtiene todo el contenido de un tipo, paginando internamente.
    Usa page_size grande para minimizar llamadas a Supabase.
    """
    all_items = []
    page = 1
    page_size = 500

    while True:
        result = content_svc.get_content_list(
            content_type=content_type,
            page=page,
            page_size=page_size,
            group=group_name,
            country=None,
            search=None,
            username='',
            password=''
        )
        items = result.get('items', [])
        all_items.extend(items)

        if not result.get('has_next', False) or len(items) < page_size:
            break
        page += 1

    return all_items