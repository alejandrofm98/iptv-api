from fastapi import APIRouter, Depends, Query, Request

from app.services.content_service import ContentServiceV2
from app.services.watch_progress_service import WatchProgressServiceV2
from utils.dependencies import AuthResult as AuthDep
from utils.dependencies import (
    get_content_service_v2,
    get_watch_progress_service_v2,
    require_auth_with_jwt,
)
from utils.exceptions import NotFoundException

router = APIRouter()


@router.get("/api/series/{serie_name}/episodes", tags=["Content"])
async def get_serie_episodes(
    serie_name: str,
    request: Request,
    page: int | None = Query(None, ge=1, description="Número de página"),
    page_size: int | None = Query(None, ge=1, le=100, description="Items por página"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
):
    if "page" not in request.query_params and "page_size" not in request.query_params:
        episodes = content_svc.get_episodes_by_serie_name(
            serie_name=serie_name,
            username=auth.username,
            password=password or "",
        )

        if not episodes:
            raise NotFoundException("Serie", serie_name)

        return {
            "serie_name": serie_name,
            "total_episodes": len(episodes),
            "seasons": list(set([ep.get("temporada") for ep in episodes if ep.get("temporada")])),
            "episodes": episodes,
        }

    episodes = content_svc.get_episodes_by_serie_name_paginated(
        serie_name=serie_name,
        username=auth.username,
        password=password or "",
        page=page or 1,
        page_size=page_size or 50,
    )

    if episodes.get("total", 0) == 0:
        raise NotFoundException("Serie", serie_name)

    return episodes


@router.get("/api/series/by-id/{series_id}/episodes", tags=["Content"])
async def get_serie_episodes_by_id(
    series_id: str,
    request: Request,
    page: int | None = Query(None, ge=1, description="Número de página"),
    page_size: int | None = Query(None, ge=1, le=100, description="Items por página"),
    password: str | None = Query(None, description="Password para construir stream_url"),
    auth: AuthDep = Depends(require_auth_with_jwt),
    content_svc: ContentServiceV2 = Depends(get_content_service_v2),
    wp_svc: WatchProgressServiceV2 = Depends(get_watch_progress_service_v2),
):
    """Get episodes for a series by any identifier (UUID, tmdb_id, provider_id, or name)."""
    row = content_svc._find_series_catalog(series_id)
    if not row:
        raise NotFoundException("Serie", series_id)

    catalog_id = str(row["id"])
    series_title = row.get("title") or row.get("tmdb_title") or series_id

    wp_rows = wp_svc.wp_repo.get_all_for_user_and_series(
        auth.user_id, series_name=series_title, catalog_id=catalog_id
    )
    watched_map: dict[tuple[int, int], bool] = {}
    for wp in wp_rows:
        sn = wp.season_number
        en = wp.episode_number
        if sn is not None and en is not None:
            watched_map[(sn, en)] = bool(wp.is_watched)

    if "page" not in request.query_params and "page_size" not in request.query_params:
        items, total, seasons = content_svc.series_repo.get_episodes_with_streams(
            catalog_id, page=1, page_size=1000
        )
        mapped = [
            content_svc._to_android_episode(
                r,
                series_title,
                catalog_id,
                auth.username,
                password or "",
                base_url=content_svc._https_base_url,
                watched_map=watched_map,
            )
            for r in (items or [])
        ]
        return {
            "serie_name": series_title,
            "total_episodes": len(mapped),
            "seasons": seasons,
            "episodes": mapped,
        }

    items, total, seasons = content_svc.series_repo.get_episodes_with_streams(
        catalog_id, page=page or 1, page_size=page_size or 50
    )
    mapped = [
        content_svc._to_android_episode(
            r,
            series_title,
            catalog_id,
            auth.username,
            password or "",
            base_url=content_svc._https_base_url,
            watched_map=watched_map,
        )
        for r in (items or [])
    ]
    return {
        "episodes": mapped,
        "total": total or 0,
        "seasons": seasons or [],
        "serie_name": series_title,
    }
