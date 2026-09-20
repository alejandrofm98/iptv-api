from unittest.mock import Mock, patch

import pytest

from iptv_api.core.exceptions import BadRequestException, ServiceUnavailableException
from iptv_api.routers.addons import get_addon_meta
from iptv_api.services.cinemeta_service import CinemetaService
from iptv_api.services.tmdb_es_service import TmdbEsService


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
    settings.tmdb_base_url = "https://api.themoviedb.org/3"
    settings.tmdb_api_key = ""
    settings.tmdb_read_token = ""
    settings.tmdb_es_timeout_seconds = 15.0
    settings.tmdb_es_cache_ttl_seconds = 2592000
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


def test_cinemeta_rejects_invalid_imdb_id():
    with (
        patch(
            "iptv_api.services.cinemeta_service.get_settings",
            return_value=make_settings(),
        ),
        pytest.raises(ValueError),
    ):
        CinemetaService(session=Mock()).get_meta("movie", "278")


def test_tmdb_es_returns_none_without_credentials():
    with patch(
        "iptv_api.services.tmdb_es_service.get_settings",
        return_value=make_settings(),
    ):
        assert TmdbEsService(session=Mock()).get_spanish(278, "movie") is None


def test_tmdb_es_returns_spanish_overview():
    session = Mock()
    session.get.return_value = make_response(
        {"title": "Cadena perpetua", "overview": "Tras el secuestro..."}
    )

    with patch(
        "iptv_api.services.tmdb_es_service.get_settings",
        return_value=make_settings(tmdb_api_key="key"),
    ):
        TmdbEsService._cache.clear()
        item = TmdbEsService(session=session).get_spanish(278, "movie")

    assert item == {"title_es": "Cadena perpetua", "overview_es": "Tras el secuestro..."}
    assert session.get.call_args.kwargs["params"] == {
        "language": "es-ES",
        "api_key": "key",
    }


def test_addon_meta_merges_cinemeta_tmdb_and_torrent():
    cinemeta = Mock()
    cinemeta.get_meta.return_value = {
        "imdb_id": "tt0111161",
        "name": "The Shawshank Redemption",
        "year": "1994",
        "description_en": "After a banker...",
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
    tmdb_es = Mock()
    tmdb_es.get_spanish.return_value = {
        "title_es": "Cadena perpetua",
        "overview_es": "Sinopsis en espanol",
    }
    torrentio = Mock()
    torrentio.get_movie_streams.return_value = [
        {"language": "ES"},
        {"language": "EN"},
    ]

    with patch("iptv_api.routers.addons.TorrentioService", return_value=torrentio):
        result = get_addon_meta(
            "movie", "tt0111161", False, True, auth=Mock(), cinemeta_svc=cinemeta, tmdb_es_svc=tmdb_es
        )

    assert result["overview_es"] == "Sinopsis en espanol"
    assert result["overview_source"] == "tmdb"
    assert result["has_torrent_source"] is True
    assert result["torrent_languages"] == ["EN", "ES"]
    assert result["torrent_status"] == "ok"


def test_addon_meta_skips_torrent_lookup_by_default():
    cinemeta = Mock()
    cinemeta.get_meta.return_value = {
        "imdb_id": "tt0111161",
        "name": "Film",
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

    with patch("iptv_api.routers.addons.TorrentioService") as torrentio_cls:
        result = get_addon_meta(
            "movie", "tt0111161", False, auth=Mock(), cinemeta_svc=cinemeta, tmdb_es_svc=Mock()
        )

    torrentio_cls.assert_not_called()
    assert result["torrent_status"] == "not_requested"
    assert result["has_torrent_source"] is False


def test_addon_meta_degrades_when_torrentio_is_down():
    cinemeta = Mock()
    cinemeta.get_meta.return_value = {
        "imdb_id": "tt0111161",
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
    tmdb_es = Mock()
    torrentio = Mock()
    torrentio.get_movie_streams.side_effect = RuntimeError("boom")

    with patch("iptv_api.routers.addons.TorrentioService", return_value=torrentio):
        result = get_addon_meta(
            "movie", "tt0111161", False, True, auth=Mock(), cinemeta_svc=cinemeta, tmdb_es_svc=tmdb_es
        )

    assert result["torrent_status"] == "unavailable"
    assert result["has_torrent_source"] is False
    assert result["overview_source"] == "none"
    tmdb_es.get_spanish.assert_not_called()


def test_addon_meta_rejects_invalid_imdb_id():
    cinemeta = Mock()
    cinemeta.get_meta.side_effect = ValueError("imdb_id debe tener formato tt1234567")

    with pytest.raises(BadRequestException):
        get_addon_meta(
            "movie", "278", False, auth=Mock(), cinemeta_svc=cinemeta, tmdb_es_svc=Mock()
        )


def test_addon_meta_maps_cinemeta_outage_to_503():
    cinemeta = Mock()
    cinemeta.get_meta.side_effect = RuntimeError("boom")

    with pytest.raises(ServiceUnavailableException):
        get_addon_meta(
            "movie",
            "tt0111161",
            False,
            auth=Mock(),
            cinemeta_svc=cinemeta,
            tmdb_es_svc=Mock(),
        )
