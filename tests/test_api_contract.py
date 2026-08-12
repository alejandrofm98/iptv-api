from iptv_api.main import app

ANDROID_CONTRACT_PATHS = {
    "/api/auth/login",
    "/api/home",
    "/api/content",
    "/api/search",
    "/api/content/{content_type}/{item_id}",
    "/api/series/by-id/{series_id}/episodes",
    "/api/watch-progress",
    "/api/watch-progress/continue",
    "/api/playback-preferences/{content_type}/{catalog_id}",
    "/api/channel-favorites",
    "/api/calendar/{fecha}",
    "/{content_type}/{username}/{password}/{stream_id}",
}


def test_android_contract_paths_remain_exposed() -> None:
    paths = set(app.openapi()["paths"])

    missing = ANDROID_CONTRACT_PATHS - paths
    assert not missing, f"Missing Android contract paths: {sorted(missing)}"


def test_android_catalog_endpoints_expose_stable_response_fields() -> None:
    schema = app.openapi()

    for path in ("/api/home", "/api/content", "/api/search"):
        operations = schema["paths"][path].values()
        assert any("responses" in operation for operation in operations)

    assert "/api/auth/login" in schema["paths"]
