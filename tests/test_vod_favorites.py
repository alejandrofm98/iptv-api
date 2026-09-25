from fastapi.testclient import TestClient

from iptv_api.core.dependencies import get_vod_favorites_service, require_auth_with_jwt
from iptv_api.core.models import AuthResult
from iptv_api.main import app


class StubVodFavoritesService:
    def __init__(self) -> None:
        self.items = []

    def list_items(self, user_id: str, username: str = "") -> list[dict]:
        assert user_id == "user-1"
        assert username == "catalog-only"
        return list(self.items)

    def add_item(self, user_id: str, content_type: str, content_id: str) -> bool:
        assert user_id == "user-1"
        key = (content_type, content_id)
        if any((item["content_type"], item["content_id"]) == key for item in self.items):
            return False
        self.items.append({"content_type": content_type, "content_id": content_id, "item": {}})
        return True

    def remove_item(self, user_id: str, content_type: str, content_id: str) -> bool:
        before = len(self.items)
        self.items = [
            item
            for item in self.items
            if (item["content_type"], item["content_id"]) != (content_type, content_id)
        ]
        return len(self.items) != before


def no_iptv_auth() -> AuthResult:
    return AuthResult(
        valid=True,
        user_id="user-1",
        username="catalog-only",
        message="OK",
        can_connect=True,
        max_devices=1,
        iptv_enabled=False,
    )


def setup_function() -> None:
    service = StubVodFavoritesService()
    app.dependency_overrides[require_auth_with_jwt] = no_iptv_auth
    app.dependency_overrides[get_vod_favorites_service] = lambda: service
    app.state.test_vod_favorites_service = service


def teardown_function() -> None:
    app.dependency_overrides.clear()
    if hasattr(app.state, "test_vod_favorites_service"):
        del app.state.test_vod_favorites_service


def test_vod_favorites_work_without_iptv_and_are_idempotent() -> None:
    client = TestClient(app)

    add = client.post(
        "/api/vod-favorites",
        json={"content_type": "series", "content_id": "tt1234567"},
    )
    assert add.status_code == 200
    assert add.json()["created"] is True

    duplicate = client.post(
        "/api/vod-favorites",
        json={"content_type": "series", "content_id": "tt1234567"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] is False

    listing = client.get("/api/vod-favorites")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["content_id"] == "tt1234567"

    removed = client.delete("/api/vod-favorites/series/tt1234567")
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True


def test_vod_favorites_reject_unknown_content_type() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/vod-favorites",
        json={"content_type": "channels", "content_id": "1"},
    )

    assert response.status_code == 422
