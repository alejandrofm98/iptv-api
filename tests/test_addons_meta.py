from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from iptv_api.core.exceptions import (
    BadRequestException,
    NotFoundException,
    ServiceUnavailableException,
)
from iptv_api.routers.addons import get_addon_catalog, get_addon_meta
from iptv_api.services.cinemeta_service import CinemetaService


def make_response(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def make_settings(**overrides: object) -> Mock:
    settings = Mock()
    settings.cinemeta_base_url = "https://v3-cinemeta.strem.io"
    settings.cinemeta_timeout_seconds = 15.0
    settings.cinemeta_cache_ttl_seconds = 86400
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def cinemeta_payload() -> dict:
    return {
        "meta": {
            "id": "tt0111161",
            "imdb_id": "tt0111161",
            "type": "movie",
            "name": "The Shawshank Redemption",
            "description": "After a banker is sentenced...",
            "year": "1994",
            "genres": ["Drama"],
            "cast": ["Tim Robbins"],
            "imdbRating": "9.3",
            "moviedb_id": 278,
            "poster": "https://images.metahub.space/poster/small/tt0111161/img",
            "background": "https://images.metahub.space/background/medium/tt0111161/img",
            "videos": [],
        }
    }


def test_cinemeta_meta_is_normalized_and_cached():
    session = Mock()
    session.get.return_value = make_response(cinemeta_payload())

    with patch(
        "iptv_api.services.cinemeta_service.get_settings",
        return_value=make_settings(),
    ):
        CinemetaService._cache.clear()
        service = CinemetaService(session=session)
        first = service.get_meta("movie", "tt0111161")
        second = service.get_meta("movie", "tt0111161")

    session.get.assert_called_once()
    assert first["name"] == "The Shawshank Redemption"
    assert first["moviedb_id"] == 278
    assert first["description_en"].startswith("After a banker")
    assert first == second


def test_cinemeta_catalog_is_normalized_and_cached():
    session = Mock()
    session.get.return_value = make_response(
        {
            "metas": [
                {
                    "id": "tt0111161",
                    "name": "The Shawshank Redemption",
                    "description": "A story",
                    "releaseInfo": "1994",
                    "genres": ["Drama"],
                    "imdbRating": "9.3",
                    "poster": "poster",
                }
            ]
        }
    )

    with patch(
        "iptv_api.services.cinemeta_service.get_settings",
        return_value=make_settings(),
    ):
        CinemetaService._cache.clear()
        service = CinemetaService(session=session)
        first = service.get_catalog("movie")
        second = service.get_catalog("movie")

    session.get.assert_called_once()
    assert first[0]["imdb_id"] == "tt0111161"
    assert first[0]["year"] == 1994
    assert first == second


def test_cinemeta_catalog_supports_title_search():
    session = Mock()
    session.get.return_value = make_response(
        {
            "metas": [
                {
                    "id": "tt32333324",
                    "name": "Batman: Knightfall - Part 1: Knightfall",
                    "description": "A story",
                    "releaseInfo": "2026",
                }
            ]
        }
    )

    with patch(
        "iptv_api.services.cinemeta_service.get_settings",
        return_value=make_settings(),
    ):
        CinemetaService._cache.clear()
        items = CinemetaService(session=session).get_catalog("movie", search="Batman: Knightfall")

    assert items[0]["imdb_id"] == "tt32333324"
    assert session.get.call_args.args[0].endswith(
        "/catalog/movie/top/search=Batman%3A%20Knightfall.json"
    )


def test_addon_catalog_search_uses_scraper_database_only():
    session = Mock()
    repository = Mock()
    repository.search_page.return_value = [
        SimpleNamespace(
            imdb_id="tt0111161",
            moviedb_id=278,
            title="The Shawshank Redemption",
            title_es="Cadena perpetua",
            content_type="movie",
            overview_es="Dos hombres crean un vínculo durante décadas.",
            description_en="English overview",
            poster="poster",
            backdrop="backdrop",
            logo=None,
            genres=[],
            rating=9.3,
            year=1994,
        )
    ]
    repository.get_metadata_by_imdb_ids.return_value = {}

    with patch(
        "iptv_api.services.addon_catalog_service.ExternalCatalogRepository", return_value=repository
    ):
        result = get_addon_catalog(
            "movie",
            "top",
            0,
            50,
            "Cadena perpetua",
            auth=Mock(),
            session=session,
        )

    repository.search_page.assert_called_once_with("movie", "top", "Cadena perpetua", 0, 50)
    repository.list_page.assert_not_called()
    assert result["items"][0]["title"] == "Cadena perpetua"
    assert result["items"][0]["description"] == "Dos hombres crean un vínculo durante décadas."
    assert result["items"][0]["overview_en"] == "English overview"


def test_cinemeta_rejects_invalid_imdb_id():
    with (
        patch(
            "iptv_api.services.cinemeta_service.get_settings",
            return_value=make_settings(),
        ),
        pytest.raises(ValueError),
    ):
        CinemetaService(session=Mock()).get_meta("movie", "278")


def test_addon_meta_uses_scraper_persisted_spanish_and_torrent():
    persisted = {
        "imdb_id": "tt0111161",
        "name": "Cadena perpetua",
        "year": "1994",
        "description_en": "After a banker...",
        "overview_es": "Sinopsis en espanol",
        "overview_source": "tmdb",
        "genres": ["Drama"],
        "cast": [],
        "imdb_rating": "9.3",
        "moviedb_id": 278,
        "poster": "poster",
        "background": "bg",
        "logo": None,
        "total_episodes": 0,
        "seasons": [],
        "episodes": [],
    }
    db_session = Mock()
    service = Mock()
    service.meta.return_value = persisted
    torrentio = Mock()
    torrentio.get_movie_streams.return_value = [
        {"language": "ES"},
        {"language": "EN"},
    ]

    with (
        patch("iptv_api.routers.addons.TorrentioService", return_value=torrentio),
        patch("iptv_api.routers.addons.AddonCatalogService", return_value=service),
    ):
        result = get_addon_meta(
            "movie",
            "tt0111161",
            False,
            True,
            auth=Mock(),
            session=db_session,
        )

    assert result["overview_es"] == "Sinopsis en espanol"
    assert result["overview_source"] == "tmdb"
    assert result["has_torrent_source"] is True
    assert result["torrent_languages"] == ["EN", "ES"]
    assert result["torrent_status"] == "ok"
    service.meta.assert_called_once_with("movie", "tt0111161", False)
    db_session.commit.assert_not_called()


def test_addon_meta_skips_torrent_lookup_by_default():
    service = Mock()
    service.meta.return_value = {"imdb_id": "tt0111161", "total_episodes": 0}
    with (
        patch("iptv_api.routers.addons.TorrentioService") as torrentio_cls,
        patch("iptv_api.routers.addons.AddonCatalogService", return_value=service),
    ):
        result = get_addon_meta("movie", "tt0111161", False, auth=Mock(), session=Mock())

    torrentio_cls.assert_not_called()
    assert result["torrent_status"] == "not_requested"
    assert result["has_torrent_source"] is False


def test_addon_series_meta_uses_scraped_spanish_episode_synopsis():
    session = Mock()
    repository = Mock()
    repository.get_by_imdb.return_value = SimpleNamespace(
        title_es="Breaking Bad",
        title="Breaking Bad",
        year=2008,
        description_en="Series overview",
        overview_es="Sinopsis",
        poster="poster",
        backdrop="background",
        logo=None,
        genres=[],
        cast=[],
        rating=9.5,
        moviedb_id=1396,
    )
    repository.list_episodes.return_value = [
        SimpleNamespace(
            video_id="tt0959621:1:1",
            season_number=1,
            episode_number=1,
            title_es="Capítulo uno",
            title_en="Episode one",
            overview_es="Texto en español",
            overview_en="English text",
            thumbnail="thumbnail",
            released="2008-01-20",
        ),
        SimpleNamespace(
            video_id="tt0959621:1:2",
            season_number=1,
            episode_number=2,
            title_es=None,
            title_en="Episode two",
            overview_es=None,
            overview_en="English fallback",
            thumbnail=None,
            released=None,
        ),
    ]
    with patch(
        "iptv_api.services.addon_catalog_service.ExternalCatalogRepository",
        return_value=repository,
    ):
        result = get_addon_meta("series", "tt0903747", True, auth=Mock(), session=session)

    assert result["episodes"][0]["overview"] == "Texto en español"
    assert result["episodes"][0]["overview_es"] == "Texto en español"
    assert result["episodes"][0]["overview_en"] == "English text"
    assert result["episodes"][1]["overview"] == "English fallback"
    assert result["total_episodes"] == 2
    session.commit.assert_not_called()


def test_addon_meta_degrades_when_torrentio_is_down():
    persisted = {
        "imdb_id": "tt0111161",
        "overview_source": "none",
        "name": "Film",
        "year": "1994",
        "description_en": "EN",
        "genres": [],
        "cast": [],
        "imdb_rating": None,
        "moviedb_id": None,
        "poster": None,
        "background": None,
        "logo": None,
        "total_episodes": 0,
        "seasons": [],
        "episodes": [],
    }
    torrentio = Mock()
    torrentio.get_movie_streams.side_effect = RuntimeError("boom")
    service = Mock()
    service.meta.return_value = persisted
    with (
        patch("iptv_api.routers.addons.TorrentioService", return_value=torrentio),
        patch("iptv_api.routers.addons.AddonCatalogService", return_value=service),
    ):
        result = get_addon_meta("movie", "tt0111161", False, True, auth=Mock(), session=Mock())

    assert result["torrent_status"] == "unavailable"
    assert result["has_torrent_source"] is False
    assert result["overview_source"] == "none"


def test_addon_meta_rejects_invalid_imdb_id():
    with pytest.raises(BadRequestException):
        get_addon_meta("movie", "278", False, auth=Mock(), session=Mock())


def test_addon_meta_returns_404_when_not_imported():
    service = Mock()
    service.meta.return_value = None
    with (
        patch("iptv_api.routers.addons.AddonCatalogService", return_value=service),
        pytest.raises(NotFoundException),
    ):
        get_addon_meta("movie", "tt0111161", False, auth=Mock(), session=Mock())


def test_addon_meta_maps_database_failure_to_503():
    service = Mock()
    service.meta.side_effect = RuntimeError("boom")
    with (
        patch("iptv_api.routers.addons.AddonCatalogService", return_value=service),
        pytest.raises(ServiceUnavailableException),
    ):
        get_addon_meta("movie", "tt0111161", False, auth=Mock(), session=Mock())
