import sys
import types
from unittest.mock import MagicMock


psycopg2_module = types.ModuleType("psycopg2")
psycopg2_module.paramstyle = "pyformat"
psycopg2_module.apilevel = "2.0"
psycopg2_module.threadsafety = 2
psycopg2_module.__version__ = "2.9.0"
psycopg2_pool_module = types.ModuleType("psycopg2.pool")
psycopg2_pool_module.SimpleConnectionPool = object
psycopg2_extras_module = types.ModuleType("psycopg2.extras")
psycopg2_extras_module.RealDictCursor = object
psycopg2_extras_module.execute_batch = lambda *args, **kwargs: None
psycopg2_module.pool = psycopg2_pool_module

sys.modules.setdefault("psycopg2", psycopg2_module)
sys.modules.setdefault("psycopg2.pool", psycopg2_pool_module)
sys.modules.setdefault("psycopg2.extras", psycopg2_extras_module)

from app.services.content_service import ContentServiceV2  # noqa: E402


def make_service() -> ContentServiceV2:
    return ContentServiceV2(MagicMock())


def test_android_catalog_item_prefers_normalized_fields_for_display():
    service = make_service()

    row = {
        "numero": 7,
        "nombre": "ES - Canal Demo HD",
        "nombre_normalizado": "Canal Demo HD",
        "grupo": "ES - Noticias",
        "grupo_normalizado": "Noticias",
        "logo": "https://img.test/channel.png",
        "url": "https://provider.test/live/user/pass/7.ts",
        "country": "ES",
    }

    item = service._to_android_catalog_item(row, "channels")

    assert item["title"] == "Canal Demo HD"
    assert item["subtitle"] == "Noticias"
    assert item["group"] == "Noticias"
    assert item["language_label"] == "ES"
    assert item["original_title"] == "ES - Canal Demo HD"
    assert item["original_group"] == "ES - Noticias"


def test_parse_content_item_prefers_persisted_stream_url_over_generated_one():
    service = make_service()

    row = {
        "numero": 7,
        "nombre": "Canal Demo",
        "logo": "",
        "grupo": "Noticias",
        "url": "https://provider.test/live/user/pass/7.ts",
        "stream_url": "https://iptv.walerike.com/live/admin/secret/7",
    }

    item = service._parse_content_item(row, "channels", username="other", password="creds")

    assert item["stream_url"] == "https://iptv.walerike.com/live/admin/secret/7"


def test_parse_content_item_interpolates_persisted_stream_url_templates():
    service = make_service()

    row = {
        "numero": 7,
        "nombre": "Canal Demo",
        "logo": "",
        "grupo": "Noticias",
        "url": "https://provider.test/live/user/pass/7.ts",
        "stream_url": "https://iptv.walerike.com/live/{{USERNAME}}/{{PASSWORD}}/7",
    }

    item = service._parse_content_item(row, "channels", username="demo", password="secret")

    assert item["stream_url"] == "https://iptv.walerike.com/live/demo/secret/7"


def test_android_catalog_item_keeps_series_name_for_grouping():
    service = make_service()

    row = {
        "numero": 9,
        "nombre": "Serie Uno S01 E01",
        "nombre_normalizado": "Serie Uno S01 E01",
        "serie_name": "Serie Uno",
        "temporada": "01",
        "episodio": "01",
        "logo": "",
        "grupo": "Series",
        "grupo_normalizado": "Series",
        "url": "https://provider.test/series/user/pass/9.mkv",
    }

    item = service._to_android_catalog_item(row, "series")

    assert item["series_name"] == "Serie Uno"


def test_android_series_group_item_includes_tmdb_metadata():
    service = make_service()

    row = {
        "id": "serie-1",
        "provider_id": "100",
        "title": "Serie Uno",
        "logo": "https://img.test/serie.png",
        "group_normalizado": "Drama",
        "total_episodes": 8,
        "year": 2025,
        "country": "ES",
        "overview_es": "Descripcion ES",
        "overview_en": "Description EN",
        "vote_average": 8.2,
        "vote_count": 120,
        "genres": ["Drama"],
        "poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "tagline": "Tagline",
        "release_date": "2025-01-01",
        "tmdb_id": 123,
        "tmdb_title": "TMDB Serie Uno",
        "popularity": 10.5,
        "status": "Returning Series",
        "total_seasons": 2,
    }

    item = service._to_android_series_group_item(row)

    assert item["type"] == "series_group"
    assert item["title"] == "TMDB Serie Uno"
    assert item["series_name"] == "TMDB Serie Uno"
    assert item["group"] == "Drama"
    assert item["normalized_group"] == "Drama"
    assert item["year"] == 2025
    assert item["overview"] == "Descripcion ES"
    assert item["rating"] == 8.2
    assert item["poster_path"] == "/poster.jpg"
    assert item["backdrop_path"] == "/backdrop.jpg"
    assert item["tmdb_id"] == 123
    assert item["tmdb_title"] == "TMDB Serie Uno"
    assert item["total_seasons"] == 2


def test_android_series_group_item_no_tmdb_fallback():
    service = make_service()

    row = {
        "id": "serie-2",
        "provider_id": "200",
        "title": "Serie Sin TMDB",
        "logo": "https://img.test/serie.png",
        "group_normalizado": "Accion",
        "total_episodes": 3,
        "year": 2020,
        "country": "MX",
    }

    item = service._to_android_series_group_item(row)

    assert item["type"] == "series_group"
    assert item["title"] == "Serie Sin TMDB"
    assert item["series_name"] == "Serie Sin TMDB"
    assert item["group"] == "Accion"
    assert item["normalized_group"] == "Accion"
    assert item["original_group"] == "Accion"
    assert item["year"] == 2020
    assert item["language_label"] == "MX"
    assert item["tmdb_title"] == ""
    assert item["tmdb_id"] is None
    assert item["overview"] is None


def test_android_movie_item_includes_tmdb_title():
    service = make_service()

    row = {
        "provider_id": "movie-1",
        "numero": 10,
        "nombre": "ES - Movie Uno",
        "nombre_normalizado": "Movie Uno",
        "grupo": "Cine",
        "grupo_normalizado": "Cine",
        "logo": "",
        "url": "https://provider.test/movie/user/pass/10.mp4",
        "tmdb_id": 321,
        "tmdb_title": "TMDB Movie Uno",
    }

    item = service._to_android_catalog_item(row, "movies")

    assert item["title"] == "Movie Uno"
    assert item["tmdb_title"] == "TMDB Movie Uno"


def test_android_movie_item_uses_tmdb_poster_when_logo_is_placeholder():
    service = make_service()

    row = {
        "provider_id": "2053693",
        "nombre": "ES - Los colores del tiempo (LQ) (2025)",
        "nombre_normalizado": "Los colores del tiempo (2025)",
        "grupo": "Peliculas 2025",
        "grupo_normalizado": "Peliculas 2025",
        "logo": "http://iptv.test/logo?url=http%3A%2F%2Fiptv.test%2Fplaceholder%2Fchannel.png&type=movie",
        "url": "https://provider.test/movie/user/pass/2053693.mkv",
        "tmdb_poster_path": "/22YFO9PqCw22IE1uDah6RRvCd1c.jpg",
    }

    item = service._to_android_catalog_item(row, "movies")

    assert item["image_url"] == "https://image.tmdb.org/t/p/w500/22YFO9PqCw22IE1uDah6RRvCd1c.jpg"


def test_android_movie_item_uses_tmdb_poster_for_movies():
    service = make_service()

    row = {
        "provider_id": "movie-1",
        "nombre": "Movie Uno",
        "nombre_normalizado": "Movie Uno",
        "grupo": "Cine",
        "grupo_normalizado": "Cine",
        "logo": "https://cdn.test/movie-logo.jpg",
        "url": "https://provider.test/movie/user/pass/10.mp4",
        "tmdb_poster_path": "/poster.jpg",
    }

    item = service._to_android_catalog_item(row, "movies")

    assert item["image_url"] == "https://image.tmdb.org/t/p/w500/poster.jpg"
