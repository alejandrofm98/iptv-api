"""User-saved movies and series. Available with or without IPTV entitlement."""

from fastapi import APIRouter, Depends

from iptv_api.core.dependencies import (
    get_vod_favorites_service,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import BadRequestException
from iptv_api.core.models import AuthResult, VodFavoriteCreate
from iptv_api.services.vod_favorites_service import VodFavoritesService

router = APIRouter()


@router.get("/api/vod-favorites", tags=["VOD Favorites"])
async def list_vod_favorites(
    auth: AuthResult = Depends(require_auth_with_jwt),
    favorites: VodFavoritesService = Depends(get_vod_favorites_service),
):
    items = favorites.list_items(auth.user_id, username=auth.username)
    return {"items": items, "total": len(items)}


@router.post("/api/vod-favorites", tags=["VOD Favorites"])
async def add_vod_favorite(
    body: VodFavoriteCreate,
    auth: AuthResult = Depends(require_auth_with_jwt),
    favorites: VodFavoritesService = Depends(get_vod_favorites_service),
):
    created = favorites.add_item(auth.user_id, body.content_type, body.content_id)
    return {
        "created": created,
        "content_type": body.content_type,
        "content_id": body.content_id,
    }


@router.delete("/api/vod-favorites/{content_type}/{content_id}", tags=["VOD Favorites"])
async def remove_vod_favorite(
    content_type: str,
    content_id: str,
    auth: AuthResult = Depends(require_auth_with_jwt),
    favorites: VodFavoritesService = Depends(get_vod_favorites_service),
):
    if content_type not in {"movies", "series"}:
        raise BadRequestException("Tipo de contenido inválido")
    deleted = favorites.remove_item(auth.user_id, content_type, content_id)
    return {"deleted": deleted, "content_type": content_type, "content_id": content_id}
