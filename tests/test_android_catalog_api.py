import sys
import types

from fastapi.testclient import TestClient


psycopg2_module = types.ModuleType("psycopg2")
psycopg2_pool_module = types.ModuleType("psycopg2.pool")
psycopg2_pool_module.SimpleConnectionPool = object
psycopg2_extras_module = types.ModuleType("psycopg2.extras")
psycopg2_extras_module.RealDictCursor = object
psycopg2_module.pool = psycopg2_pool_module

sys.modules.setdefault("psycopg2", psycopg2_module)
sys.modules.setdefault("psycopg2.pool", psycopg2_pool_module)
sys.modules.setdefault("psycopg2.extras", psycopg2_extras_module)

from scripts.api import app
from utils.dependencies import get_calendar_service, get_channel_favorites_service, get_content_service, require_auth_with_jwt
from utils.models import AuthResult


class StubContentService:
    def __init__(self):
        self.last_home_country = None

    def get_home_catalog(self, username: str, page_size: int = 12, country: str | None = None, password: str = '') -> dict:
        self.last_home_country = country
        return {
            "featured_channels": [{"id": "1", "title": "Noticias 24", "type": "channel"}],
            "featured_movies": [{"id": "2", "title": "Pelicula Uno", "type": "movie"}],
            "featured_series": [{"id": "3", "title": "Serie Uno", "type": "series"}],
            "stats": {"channels": 10, "movies": 20, "series": 30},
        }

    def search_catalog(
        self,
        query: str,
        types: list[str],
        page: int,
        page_size: int,
        username: str,
        password: str = '',
    ) -> dict:
        return {
            "items": [
                {"id": "1", "title": f"{query} Noticias", "type": "channel"},
                {"id": "2", "title": f"{query} Serie", "type": "series"},
            ],
            "total": 2,
            "page": page,
            "page_size": page_size,
            "pages": 1,
            "has_next": False,
            "has_prev": False,
            "types": types,
        }

    def get_android_content_list(
        self,
        content_type: str,
        page: int,
        page_size: int,
        group: str | None,
        country: str | None,
        search: str | None,
        username: str,
        password: str = '',
    ) -> dict:
        return {
            "items": [
                {
                    "id": "10",
                    "type": "series" if content_type == "series" else content_type[:-1],
                    "title": "Serie Uno S01 E01" if content_type == "series" else "Canal Uno",
                    "normalized_title": "Serie Uno S01 E01" if content_type == "series" else "Canal Uno",
                    "subtitle": "Drama" if content_type == "series" else "Noticias",
                    "normalized_group": "Drama" if content_type == "series" else "Noticias",
                    "group": "Drama" if content_type == "series" else "Noticias",
                    "language_label": country or "ES",
                    "series_name": "Serie Uno" if content_type == "series" else None,
                    "season_number": 1 if content_type == "series" else None,
                    "episode_number": 1 if content_type == "series" else None,
                    "stream_url": "https://stream/test.m3u8",
                }
            ],
            "total": 1,
            "page": page,
            "page_size": page_size,
            "pages": 1,
            "has_next": False,
            "has_prev": False,
        }

    def get_catalog_filters(self, content_type: str, country: str | None = None) -> dict:
        return {
            "languages": [country or "ES", "EN"],
            "groups": ["Noticias", "Drama"],
        }

    def get_groups(self, content_type: str, countries: list[str] | None = None) -> list[str]:
        return ["Noticias", "Drama"]

    def get_episodes_by_serie_name_paginated(
        self,
        serie_name: str,
        username: str,
        password: str,
        page: int,
        page_size: int,
    ) -> dict:
        return {
            "serie_name": serie_name,
            "items": [{"id": "ep-1", "title": "Episodio 1"}],
            "episodes": [{"id": "ep-1", "title": "Episodio 1"}],
            "total": 1,
            "total_episodes": 1,
            "page": page,
            "page_size": page_size,
            "pages": 1,
            "has_next": False,
            "has_prev": False,
            "seasons": [1],
        }

    def get_episodes_by_serie_name(self, serie_name: str, username: str, password: str) -> list[dict]:
        return [{"id": "ep-1", "temporada": 1, "title": "Episodio 1"}]


class StubCalendarService:
    def get_events_by_date(self, fecha: str) -> list[dict]:
        return [
            {
                "id": "event-1",
                "fecha": fecha,
                "hora": "20:00",
                "competicion": "Liga",
                "categoria": "Futbol",
                "equipos": "Equipo A vs Equipo B",
                "canales_original": ["Canal A"],
                "canales_resueltos": [
                    {
                        "channel_id": "101",
                        "display_name": "Canal A",
                        "quality": "HD",
                        "priority": 1,
                        "source_name": "provider",
                        "logo": "https://img/logo.png",
                        "stream_url": "https://stream/101.m3u8",
                    }
                ],
            }
        ]

    def get_provider_ids(self, channel_ids: list[str]) -> dict[str, str | None]:
        return {cid: None for cid in channel_ids}


class StubChannelFavoritesService:
    def __init__(self):
        self.items = [
            {
                "user_id": "user-1",
                "channel_provider_id": "fav-1",
                "provider_id": "fav-1",
                "created_at": "2026-03-28T00:00:00Z",
            }
        ]
        self.last_content_params = None
        self.last_home_params = None

    def list_favorites(self, user_id: str) -> list[dict]:
        assert user_id == "user-1"
        return list(self.items)

    def add_favorite(self, user_id: str, channel_provider_id: str) -> dict:
        item = {
            "user_id": user_id,
            "channel_provider_id": channel_provider_id,
            "provider_id": channel_provider_id,
            "created_at": "2026-03-28T00:00:00Z",
        }
        self.items = [item, *[existing for existing in self.items if existing["channel_provider_id"] != channel_provider_id]]
        return item

    def remove_favorite(self, user_id: str, channel_provider_id: str) -> bool:
        original_count = len(self.items)
        self.items = [item for item in self.items if item["channel_provider_id"] != channel_provider_id]
        return len(self.items) != original_count

    def get_favorite_channels(
        self,
        user_id: str,
        content_svc,
        page: int,
        page_size: int,
        country: str | None,
        search: str | None,
        username: str,
        password: str = '',
    ) -> dict:
        params = {
            "user_id": user_id,
            "page": page,
            "page_size": page_size,
            "country": country,
            "search": search,
            "username": username,
            "password": password,
        }
        if search is None:
            self.last_home_params = params
        else:
            self.last_content_params = params
        return {
            "items": [
                {
                    "id": "fav-1",
                    "provider_id": "fav-1",
                    "type": "channel",
                    "title": "Canal Favorito",
                    "normalized_title": "Canal Favorito",
                    "subtitle": "Favorites",
                    "group": "Favorites",
                    "normalized_group": "Favorites",
                    "stream_url": "https://stream/favorite.m3u8",
                }
            ],
            "total": 1,
            "page": page,
            "page_size": page_size,
            "pages": 1,
            "has_next": False,
            "has_prev": False,
        }


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
    stub_content_service = StubContentService()
    stub_favorites_service = StubChannelFavoritesService()
    app.dependency_overrides[require_auth_with_jwt] = override_auth
    app.dependency_overrides[get_content_service] = lambda: stub_content_service
    app.dependency_overrides[get_calendar_service] = lambda: StubCalendarService()
    app.dependency_overrides[get_channel_favorites_service] = lambda: stub_favorites_service
    client = TestClient(app)
    client.stub_content_service = stub_content_service
    client.stub_favorites_service = stub_favorites_service
    return client


def teardown_function() -> None:
    app.dependency_overrides.clear()


def test_get_home_returns_lightweight_sections() -> None:
    client = create_client()

    response = client.get("/api/home")

    assert response.status_code == 200
    payload = response.json()
    assert payload["featured_channels"][0]["title"] == "Noticias 24"
    assert payload["stats"] == {"channels": 10, "movies": 20, "series": 30}
    assert payload["favorites"][0]["provider_id"] == "fav-1"
    assert payload["favorites"][0]["type"] == "channel"
    assert "channel_provider_id" not in payload["favorites"][0]
    assert client.stub_favorites_service.last_home_params == {
        "user_id": "user-1",
        "page": 1,
        "page_size": 12,
        "country": None,
        "search": None,
        "username": "demo",
        "password": "",
    }


def test_get_home_accepts_country_filter() -> None:
    client = create_client()

    response = client.get("/api/home", params={"country": "EN"})

    assert response.status_code == 200
    assert client.stub_content_service.last_home_country == "EN"
    assert client.stub_favorites_service.last_home_params["country"] == "EN"


def test_search_returns_paginated_catalog_results() -> None:
    client = create_client()

    response = client.get("/api/search", params={"q": "deporte", "types": "channels,series", "page": 1, "page_size": 20})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "deporte Noticias"
    assert payload["types"] == ["channels", "series"]
    assert payload["page_size"] == 20


def test_content_returns_android_friendly_payload() -> None:
    client = create_client()

    response = client.get("/api/content", params={"content_type": "series", "page": 1, "page_size": 20, "country": "ES"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["series_name"] == "Serie Uno"
    assert payload["items"][0]["language_label"] == "ES"
    assert payload["items"][0]["normalized_group"] == "Drama"


def test_content_filters_returns_languages_and_groups() -> None:
    client = create_client()

    response = client.get("/api/content/filters", params={"content_type": "channels", "country": "ES"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["languages"][0] == "ES"
    assert payload["groups"][0] == "Favorites"
    assert "Drama" in payload["groups"]


def test_content_groups_prepends_favorites_for_channels() -> None:
    client = create_client()

    response = client.get("/api/content/groups", params={"content_type": "channels"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["groups"][0] == "Favorites"


def test_content_favorites_group_uses_channel_favorites_service() -> None:
    client = create_client()

    response = client.get(
        "/api/content",
        params={"content_type": "channels", "group": "Favorites", "page": 2, "page_size": 5, "country": "ES", "search": "deporte"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "Canal Favorito"
    assert client.stub_favorites_service.last_content_params == {
        "user_id": "user-1",
        "page": 2,
        "page_size": 5,
        "country": "ES",
        "search": "deporte",
        "username": "demo",
        "password": "",
    }


def test_channel_favorites_endpoints_list_add_and_delete() -> None:
    client = create_client()

    list_response = client.get("/api/channel-favorites")
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["provider_id"] == "fav-1"

    add_response = client.post("/api/channel-favorites", json={"channel_provider_id": "fav-2"})
    assert add_response.status_code == 200
    assert add_response.json()["channel_provider_id"] == "fav-2"

    delete_response = client.delete("/api/channel-favorites/fav-2")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "channel_provider_id": "fav-2"}


def test_series_episodes_endpoint_supports_pagination() -> None:
    client = create_client()

    response = client.get("/api/series/Serie%20Uno/episodes", params={"page": 2, "page_size": 15})

    assert response.status_code == 200
    payload = response.json()
    assert payload["serie_name"] == "Serie Uno"
    assert payload["page"] == 2
    assert payload["page_size"] == 15
    assert payload["items"][0]["title"] == "Episodio 1"


def test_calendar_events_include_direct_stream_data() -> None:
    client = create_client()

    response = client.get("/api/calendar/2026-03-19")

    assert response.status_code == 200
    payload = response.json()
    channel = payload["eventos"][0]["canales_resueltos"][0]
    assert channel["display_name"] == "Canal A"
    assert channel["logo"] == "https://img/logo.png"
    assert channel["stream_url"] == "https://stream/101.m3u8"
