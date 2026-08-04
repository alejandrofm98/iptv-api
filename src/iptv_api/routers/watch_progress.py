from fastapi import APIRouter, Depends, Query

from iptv_api.core.dependencies import AuthResult as AuthDep
from iptv_api.core.dependencies import (
    get_watch_progress_service_v2,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import NotFoundException
from iptv_api.core.models import WatchProgressUpsert
from iptv_api.services.watch_progress_service import WatchProgressServiceV2

router = APIRouter()


@router.get("/api/watch-progress", tags=["Watch Progress"])
async def get_continue_watching(
    limit: int = Query(20, ge=1, le=50, description="Máximo de items"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene items con progreso de visualización incompleto. Requiere Bearer Token."""
    items = wp_svc.get_continue_watching(auth.user_id, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/api/watch-progress/watched", tags=["Watch Progress"])
async def get_watched_items(
    limit: int = Query(100, ge=1, le=500, description="Máximo de items por página"),
    offset: int = Query(0, ge=0, description="Desplazamiento para paginación"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene items marcados como vistos, paginados. Requiere Bearer Token."""
    return wp_svc.get_watched_items(auth.user_id, limit=limit, offset=offset)


@router.get("/api/watch-progress/{content_id}", tags=["Watch Progress"])
async def get_watch_progress(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene el progreso de un item específico. Requiere Bearer Token."""
    progress = wp_svc.get_progress(auth.user_id, content_id)
    if not progress:
        raise NotFoundException("WatchProgress", content_id)
    return progress


@router.put("/api/watch-progress/{content_id}", tags=["Watch Progress"])
async def upsert_watch_progress(
    content_id: str,
    body: WatchProgressUpsert,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Crea o actualiza el progreso de visualización. Requiere Bearer Token."""
    result = wp_svc.upsert_progress(auth.user_id, content_id, body.model_dump())
    return result


@router.delete("/api/watch-progress/{content_id}", tags=["Watch Progress"])
async def delete_watch_progress(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Elimina el progreso de visualización de un item (todos los episodios si es serie). Requiere Bearer Token."""
    deleted = wp_svc.delete_progress(auth.user_id, content_id)
    if not deleted:
        raise NotFoundException("WatchProgress", content_id)
    return {"deleted": True}


@router.delete("/api/watch-progress/{content_id}/episode", tags=["Watch Progress"])
async def delete_watch_progress_episode(
    content_id: str,
    season: int = Query(..., ge=0, description="Temporada"),
    episode: int = Query(..., ge=0, description="Episodio"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Elimina el progreso de un episodio específico. Requiere Bearer Token."""
    deleted = wp_svc.delete_episode_progress(auth.user_id, content_id, season, episode)
    if not deleted:
        raise NotFoundException("WatchProgress", f"{content_id} S{season}E{episode}")
    return {"deleted": True}


@router.post("/api/watch-progress/{content_id}/mark-watched", tags=["Watch Progress"])
async def mark_watched(
    content_id: str,
    season: int | None = Query(None, ge=0),
    episode: int | None = Query(None, ge=0),
    completed: bool = Query(False),
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Marca un contenido como visto. Requiere Bearer Token."""
    result = wp_svc.set_is_watched(
        auth.user_id,
        content_id,
        True,
        season=season,
        episode=episode,
        completed=completed,
    )
    return {"content_id": content_id, "is_watched": True, "result": result}


@router.post("/api/watch-progress/{content_id}/mark-unwatched", tags=["Watch Progress"])
async def mark_unwatched(
    content_id: str,
    season: int | None = Query(None, ge=0),
    episode: int | None = Query(None, ge=0),
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Marca un contenido como no visto. Requiere Bearer Token."""
    result = wp_svc.set_is_watched(auth.user_id, content_id, False, season=season, episode=episode)
    return {"content_id": content_id, "is_watched": False, "result": result}


@router.get("/api/watch-progress/{content_id}/status", tags=["Watch Progress"])
async def get_watch_status(
    content_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Obtiene el estado de visto de un contenido. Requiere Bearer Token."""
    progress = wp_svc.get_progress(auth.user_id, content_id)
    if not progress:
        return {"content_id": content_id, "is_watched": False, "progress_percent": 0}
    return {
        "content_id": content_id,
        "is_watched": progress.get("is_watched", False),
        "progress_percent": progress.get("progress_percent", 0),
    }
