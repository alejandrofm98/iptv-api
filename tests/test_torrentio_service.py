from unittest.mock import Mock, patch

from iptv_api.routers.torrentio import get_episode_streams, get_movie_streams
from iptv_api.services.torrentio_service import TorrentioService


def make_response(payload: dict) -> Mock:
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def make_settings() -> Mock:
    settings = Mock()
    settings.torrentio_base_url = "https://torrentio.strem.fun"
    settings.torrentio_providers = "wolfmax4k"
    settings.torrentio_languages = "spanish,english"
    settings.torrentio_timeout_seconds = 15.0
    settings.torrentio_cache_ttl_seconds = 60
    return settings


def test_movie_streams_are_normalized_and_filter_latino():
    session = Mock()
    session.get.return_value = make_response(
        {
            "streams": [
                {
                    "title": "Pelicula [Castellano] 👤 12 💾 1.5 GB ⚙️ Wolfmax4k\n🇪🇸",
                    "name": "Torrentio\n1080p",
                    "infoHash": "a" * 40,
                    "fileIdx": 2,
                },
                {
                    "title": "Pelicula 🇲🇽 👤 8 💾 1 GB ⚙️ Wolfmax4k",
                    "name": "Torrentio\n1080p",
                    "infoHash": "b" * 40,
                },
            ]
        }
    )

    with patch("iptv_api.services.torrentio_service.get_settings", return_value=make_settings()):
        TorrentioService._cache.clear()
        items = TorrentioService(session=session).get_movie_streams("tt0111161")

    assert len(items) == 1
    assert items[0]["country"] == "ES"
    assert items[0]["info_hash"] == "a" * 40
    assert items[0]["file_idx"] == 2
    assert items[0]["size_bytes"] == int(1.5 * 1024**3)
    assert items[0]["playable"] is False


def test_stream_lookup_uses_short_memory_cache():
    session = Mock()
    session.get.return_value = make_response({"streams": []})

    with patch("iptv_api.services.torrentio_service.get_settings", return_value=make_settings()):
        TorrentioService._cache.clear()
        service = TorrentioService(session=session)
        service.get_movie_streams("tt0111161")
        service.get_movie_streams("tt0111161")

    session.get.assert_called_once()


def test_movie_endpoint_resolves_catalog_id_to_imdb_id():
    repository = Mock()
    repository.get_movie_with_metadata.return_value = {"imdb_id": "tt0111161"}
    service = Mock()
    service.get_movie_streams.return_value = [{"info_hash": "a" * 40}]

    with (
        patch("iptv_api.routers.torrentio.ContentRepository", return_value=repository),
        patch("iptv_api.routers.torrentio.TorrentioService", return_value=service),
    ):
        result = get_movie_streams("catalog-id", auth=Mock(), db=Mock())

    repository.get_movie_with_metadata.assert_called_once_with("catalog-id")
    service.get_movie_streams.assert_called_once_with("tt0111161")
    assert result["total"] == 1


def test_episode_endpoint_uses_series_imdb_id_and_episode_coordinates():
    repository = Mock()
    repository.get_with_metadata.return_value = {"imdb_id": "tt0903747"}
    service = Mock()
    service.get_episode_streams.return_value = [{"info_hash": "b" * 40}]

    with (
        patch("iptv_api.routers.torrentio.SeriesRepository", return_value=repository),
        patch("iptv_api.routers.torrentio.TorrentioService", return_value=service),
    ):
        result = get_episode_streams("series-id", 2, 3, auth=Mock(), db=Mock())

    repository.get_with_metadata.assert_called_once_with("series-id")
    service.get_episode_streams.assert_called_once_with("tt0903747", 2, 3)
    assert result["total"] == 1
