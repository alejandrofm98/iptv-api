"""Regresiones de concurrencia en preferencias de reproduccion."""

from datetime import UTC, datetime
from threading import Event, Thread, Timer
from time import monotonic
from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from iptv_api.core.dependencies import (
    get_playback_preference_service,
    require_auth_with_jwt,
)
from iptv_api.core.models import AuthResult
from iptv_api.routers.health import router as health_router
from iptv_api.routers.playback_preferences import router as playback_preferences_router


class BlockingPlaybackPreferenceService:
    """Simula una escritura SQL bloqueada por otra transaccion."""

    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    def upsert(
        self, user_id: str, content_type: str, catalog_id: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        self.entered.set()
        self.release.wait(timeout=2)
        now = datetime.now(UTC)
        return {
            "id": str(uuid4()),
            "user_id": user_id,
            "content_type": content_type,
            "catalog_id": catalog_id,
            "audio_language": data.get("audio_language"),
            "audio_label": data.get("audio_label"),
            "subtitle_language": None,
            "subtitle_label": None,
            "subtitles_disabled": None,
            "created_at": now,
            "updated_at": now,
        }


def test_blocked_preference_update_does_not_block_healthcheck() -> None:
    service = BlockingPlaybackPreferenceService()
    user_id = str(uuid4())
    catalog_id = str(uuid4())
    app = FastAPI()
    app.include_router(health_router)
    app.include_router(playback_preferences_router)
    app.dependency_overrides[require_auth_with_jwt] = lambda: AuthResult(
        valid=True,
        user_id=user_id,
        username="test-user",
        message="OK",
    )
    app.dependency_overrides[get_playback_preference_service] = lambda: service

    result: dict[str, Any] = {}

    with TestClient(app) as client:

        def update_preference() -> None:
            result["response"] = client.put(
                f"/api/playback-preferences/movie/{catalog_id}",
                json={"audio_language": "EN"},
            )

        update_thread = Thread(target=update_preference)
        update_thread.start()
        assert service.entered.wait(timeout=1)

        release_timer = Timer(1, service.release.set)
        release_timer.start()
        started_at = monotonic()
        health_response = client.get("/health")
        elapsed = monotonic() - started_at

        service.release.set()
        release_timer.cancel()
        update_thread.join(timeout=2)

    assert health_response.status_code == 200
    assert elapsed < 0.5
    assert not update_thread.is_alive()
    assert result["response"].status_code == 200
