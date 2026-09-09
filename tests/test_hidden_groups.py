from fastapi.testclient import TestClient

from iptv_api.core.dependencies import (
    get_hidden_group_service,
    require_auth_with_jwt,
)
from iptv_api.core.models import AuthResult
from iptv_api.main import app


class StubHiddenGroupService:
    def __init__(self):
        self.items = [
            {
                "user_id": "user-1",
                "country": "ES",
                "group_name": "Deportes",
                "created_at": "2026-09-09T00:00:00Z",
            }
        ]

    def list_hidden(self, user_id: str) -> list[dict]:
        assert user_id == "user-1"
        return list(self.items)

    def hide(self, user_id: str, group_name: str, country: str = "") -> dict:
        item = {
            "user_id": user_id,
            "country": country,
            "group_name": group_name,
            "created_at": "2026-09-09T00:00:00Z",
        }
        self.items = [
            item,
            *[e for e in self.items if not (e["group_name"] == group_name and e["country"] == country)],
        ]
        return item

    def unhide(self, user_id: str, group_name: str, country: str = "") -> bool:
        original = len(self.items)
        self.items = [
            e for e in self.items if not (e["group_name"] == group_name and e["country"] == country)
        ]
        return len(self.items) != original


def override_auth() -> AuthResult:
    return AuthResult(
        valid=True,
        user_id="user-1",
        username="demo",
        message="OK",
        can_connect=True,
        current_devices=0,
        max_devices=5,
    )


def create_client() -> TestClient:
    app.dependency_overrides[require_auth_with_jwt] = override_auth
    app.dependency_overrides[get_hidden_group_service] = lambda: StubHiddenGroupService()
    return TestClient(app)


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_list_hidden_groups() -> None:
    client = create_client()

    response = client.get("/api/hidden-groups")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0] == {
        "user_id": "user-1",
        "country": "ES",
        "group_name": "Deportes",
        "created_at": "2026-09-09T00:00:00Z",
    }


def test_hide_group_scoped_by_country() -> None:
    client = create_client()

    response = client.post("/api/hidden-groups", json={"group_name": "Cine", "country": "ES"})

    assert response.status_code == 200
    assert response.json() == {
        "user_id": "user-1",
        "country": "ES",
        "group_name": "Cine",
        "created_at": "2026-09-09T00:00:00Z",
    }


def test_hide_group_global_without_country() -> None:
    client = create_client()

    response = client.post("/api/hidden-groups", json={"group_name": "Adultos"})

    assert response.status_code == 200
    assert response.json()["country"] == ""


def test_unhide_group() -> None:
    client = create_client()

    response = client.delete("/api/hidden-groups", params={"group": "Deportes", "country": "ES"})

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "group_name": "Deportes", "country": "ES"}


def test_unhide_missing_group_returns_404() -> None:
    client = create_client()

    response = client.delete("/api/hidden-groups", params={"group": "Inexistente"})

    assert response.status_code == 404
