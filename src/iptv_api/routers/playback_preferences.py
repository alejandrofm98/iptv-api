"""Endpoints de preferencias de reproduccion."""

from fastapi import APIRouter, Depends

from iptv_api.core.dependencies import (
    AuthResult,
    get_playback_preference_service,
    require_auth_with_jwt,
)
from iptv_api.core.exceptions import NotFoundException
from iptv_api.schemas.playback_preference import (
    PlaybackPreferenceResponse,
    PlaybackPreferenceUpdate,
)
from iptv_api.services.playback_preference_service import PlaybackPreferenceService

router = APIRouter(prefix="/api/playback-preferences", tags=["Playback Preferences"])


@router.get("/{content_type}/{catalog_id}", response_model=PlaybackPreferenceResponse)
def get_playback_preference(
    content_type: str,
    catalog_id: str,
    auth: AuthResult = Depends(require_auth_with_jwt),
    service: PlaybackPreferenceService = Depends(get_playback_preference_service),
):
    assert auth.user_id is not None
    preference = service.get(auth.user_id, content_type, catalog_id)
    if preference is None:
        raise NotFoundException("PlaybackPreference", catalog_id)
    return preference


@router.put("/{content_type}/{catalog_id}", response_model=PlaybackPreferenceResponse)
def upsert_playback_preference(
    content_type: str,
    catalog_id: str,
    body: PlaybackPreferenceUpdate,
    auth: AuthResult = Depends(require_auth_with_jwt),
    service: PlaybackPreferenceService = Depends(get_playback_preference_service),
):
    assert auth.user_id is not None
    return service.upsert(
        auth.user_id,
        content_type,
        catalog_id,
        body.model_dump(exclude_unset=True),
    )


@router.delete("/{content_type}/{catalog_id}")
def delete_playback_preference(
    content_type: str,
    catalog_id: str,
    auth: AuthResult = Depends(require_auth_with_jwt),
    service: PlaybackPreferenceService = Depends(get_playback_preference_service),
):
    assert auth.user_id is not None
    if not service.delete(auth.user_id, content_type, catalog_id):
        raise NotFoundException("PlaybackPreference", catalog_id)
    return {"deleted": True}
