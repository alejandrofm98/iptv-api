from fastapi import APIRouter, Depends, Query

from iptv_api.core.dependencies import AuthResult as AuthDep
from iptv_api.core.dependencies import (
    get_hidden_group_service,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import NotFoundException
from iptv_api.core.models import HiddenGroupCreate
from iptv_api.services.hidden_group_service import HiddenGroupService

router = APIRouter()


@router.get("/api/hidden-groups", tags=["Hidden Groups"])
async def list_hidden_groups(
    auth: AuthDep = Depends(require_auth_with_jwt),
    hidden_svc: HiddenGroupService = Depends(get_hidden_group_service),
):
    items = hidden_svc.list_hidden(auth.user_id)
    return {"items": items, "total": len(items)}


@router.post("/api/hidden-groups", tags=["Hidden Groups"])
async def hide_group(
    body: HiddenGroupCreate,
    auth: AuthDep = Depends(require_auth_with_jwt),
    hidden_svc: HiddenGroupService = Depends(get_hidden_group_service),
):
    return hidden_svc.hide(auth.user_id, body.group_name, body.country or "")


@router.delete("/api/hidden-groups", tags=["Hidden Groups"])
async def unhide_group(
    group: str = Query(..., min_length=1, max_length=255),
    country: str = Query(default="", max_length=10),
    auth: AuthDep = Depends(require_auth_with_jwt),
    hidden_svc: HiddenGroupService = Depends(get_hidden_group_service),
):
    deleted = hidden_svc.unhide(auth.user_id, group, country)
    if not deleted:
        raise NotFoundException("HiddenChannelGroup", group)
    return {"deleted": True, "group_name": group, "country": country}
