from fastapi import APIRouter, Depends

from app.services.channel_favorites_service import ChannelFavoritesServiceV2
from utils.dependencies import AuthResult as AuthDep
from utils.dependencies import (
    get_channel_favorites_service_v2,
    require_auth_with_jwt,
)
from utils.exceptions import NotFoundException
from utils.models import ChannelFavoriteCreate

router = APIRouter()


@router.get("/api/channel-favorites", tags=["Channel Favorites"])
async def list_channel_favorites(
    auth: AuthDep = Depends(require_auth_with_jwt),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    items = favorites_svc.list_favorites(auth.user_id)
    return {"items": items, "total": len(items)}


@router.post("/api/channel-favorites", tags=["Channel Favorites"])
async def add_channel_favorite(
    body: ChannelFavoriteCreate,
    auth: AuthDep = Depends(require_auth_with_jwt),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    return favorites_svc.add_favorite(auth.user_id, body.channel_provider_id)


@router.delete("/api/channel-favorites/{channel_provider_id}", tags=["Channel Favorites"])
async def delete_channel_favorite(
    channel_provider_id: str,
    auth: AuthDep = Depends(require_auth_with_jwt),
    favorites_svc: ChannelFavoritesServiceV2 = Depends(get_channel_favorites_service_v2),
):
    deleted = favorites_svc.remove_favorite(auth.user_id, channel_provider_id)
    if not deleted:
        raise NotFoundException("ChannelFavorite", channel_provider_id)
    return {"deleted": True, "channel_provider_id": channel_provider_id}
