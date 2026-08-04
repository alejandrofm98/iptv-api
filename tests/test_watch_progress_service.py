from unittest.mock import MagicMock

from iptv_api.services.watch_progress_service import WatchProgressServiceV2


def make_progress_row(
    user_id: str = "user-1",
    content_id: str = "movie:2053693",
    content_type: str = "movie",
    position_ms: int = 300_000,
    duration_ms: int = 3_000_000,
    title: str = "Old title",
    image_url: str = "https://old/img.jpg",
    is_watched: bool = False,
    series_name: str | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> MagicMock:
    row = MagicMock()
    row.id = "wp-row-id"
    row.user_id = user_id
    row.content_id = content_id
    row.content_type = content_type
    row.position_ms = position_ms
    row.duration_ms = duration_ms
    row.title = title
    row.image_url = image_url
    row.last_watched_at = None
    row.is_watched = is_watched
    row.series_name = series_name
    row.season_number = season_number
    row.episode_number = episode_number
    return row


def make_service_with_mocks(
    progress_row, movie_meta=None, series_meta=None
) -> WatchProgressServiceV2:
    service = WatchProgressServiceV2(MagicMock())
    service.wp_repo.get_continue_watching = MagicMock(return_value=[progress_row])
    service.content_repo.get_movie_with_metadata = MagicMock(return_value=movie_meta)
    service.series_repo.get_with_metadata = MagicMock(return_value=series_meta)
    return service


def test_continue_watching_movie_includes_tmdb_metadata_and_poster_for_placeholder_logo():
    progress_row = make_progress_row()

    movie_meta = {
        "content_type": "movie",
        "id": "movie-row-id",
        "provider_id": "2053693",
        "nombre": "ES - Los colores del tiempo (LQ) (2025)",
        "nombre_normalizado": "Los colores del tiempo (2025)",
        "logo": "http://iptv.test/logo?url=http%3A%2F%2Fiptv.test%2Fplaceholder%2Fchannel.png&type=movie",
        "overview_es": "Descripcion ES",
        "overview_en": "Description EN",
        "vote_average": 7.4,
        "vote_count": 40,
        "genres": ["Drama"],
        "tmdb_poster_path": "/poster.jpg",
        "backdrop_path": "/backdrop.jpg",
        "runtime_minutes": 124,
        "tagline": "Tagline",
        "release_date": "2025-05-22",
        "year": 2025,
        "tmdb_id": 1234,
        "tmdb_title": "Los colores del tiempo",
        "popularity": 9.5,
        "status": "Released",
    }

    service = make_service_with_mocks(progress_row, movie_meta=movie_meta)

    item = service.get_continue_watching("user-1", limit=20)[0]

    assert item["content_id"] == "2053693"
    assert item["title"] == "ES - Los colores del tiempo (LQ) (2025)"
    assert item["normalized_title"] == "Los colores del tiempo (2025)"
    assert item["image_url"] == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert item["overview"] == "Descripcion ES"
    assert item["overview_es"] == "Descripcion ES"
    assert item["rating"] == 7.4
    assert item["vote_average"] == 7.4
    assert item["poster_path"] == "/poster.jpg"
    assert item["backdrop_path"] == "/backdrop.jpg"
    assert item["runtime_minutes"] == 124
    assert item["tmdb_title"] == "Los colores del tiempo"
    assert item["progress_percent"] == 10


def test_continue_watching_keeps_real_logo_when_available():
    progress_row = make_progress_row()

    movie_meta = {
        "content_type": "movie",
        "id": "movie-row-id",
        "provider_id": "2053693",
        "nombre": "Movie title",
        "nombre_normalizado": "Movie title",
        "logo": "https://cdn.test/movie.jpg",
        "tmdb_poster_path": "/poster.jpg",
    }

    service = make_service_with_mocks(progress_row, movie_meta=movie_meta)

    item = service.get_continue_watching("user-1", limit=20)[0]

    assert item["image_url"] == "https://cdn.test/movie.jpg"


def test_continue_watching_series_includes_tmdb_metadata():
    progress_row = make_progress_row(
        content_id="series:777",
        content_type="series",
        position_ms=120_000,
        duration_ms=1_200_000,
        title="Old episode",
        image_url="",
        series_name="Old Serie",
        season_number=1,
        episode_number=1,
    )

    series_meta = {
        "content_type": "series",
        "id": "episode-row-id",
        "provider_id": "777",
        "nombre": "Episode title",
        "nombre_normalizado": "Episode title",
        "serie_name": "Serie Rica",
        "temporada": 2,
        "episodio": 3,
        "logo": "",
        "overview_es": "Serie ES",
        "tmdb_poster_path": "/serie-poster.jpg",
        "backdrop_path": "/serie-backdrop.jpg",
        "tmdb_title": "Serie Rica TMDB",
        "total_seasons": 4,
    }

    service = make_service_with_mocks(progress_row, series_meta=series_meta)

    item = service.get_continue_watching("user-1", limit=20)[0]

    assert item["content_id"] == "777"
    assert item["series_name"] == "Serie Rica"
    assert item["season_number"] == 2
    assert item["episode_number"] == 3
    assert item["image_url"] == "https://image.tmdb.org/t/p/w500/serie-poster.jpg"
    assert item["overview"] == "Serie ES"
    assert item["backdrop_path"] == "/serie-backdrop.jpg"
    assert item["tmdb_title"] == "Serie Rica TMDB"
    assert item["total_seasons"] == 4


def test_continue_watching_dedupes_series_episodes_to_one_entry():
    """Varios episodios de la misma serie no deben generar entradas duplicadas."""
    rows = [
        make_progress_row(
            content_id="645757c6-d3bd-4dfa-a6a0-adabf9e640fc",
            content_type="series",
            position_ms=616_421,
            duration_ms=2_583_648,
            series_name="The Rookie (2018)",
            season_number=5,
            episode_number=17,
        ),
        make_progress_row(
            content_id="645757c6-d3bd-4dfa-a6a0-adabf9e640fc",
            content_type="series",
            position_ms=479_020,
            duration_ms=2_561_768,
            series_name="The Rookie (2018)",
            season_number=5,
            episode_number=19,
        ),
        make_progress_row(
            content_id="645757c6-d3bd-4dfa-a6a0-adabf9e640fc",
            content_type="series",
            position_ms=1_527,
            duration_ms=2_581_594,
            series_name=None,
            season_number=None,
            episode_number=None,
        ),
    ]
    rows[0].last_watched_at = "2026-08-03T13:24:02"
    rows[1].last_watched_at = "2026-08-03T13:32:35"
    rows[2].last_watched_at = "2026-08-03T13:32:41"

    service = WatchProgressServiceV2(MagicMock())
    service.wp_repo.get_continue_watching = MagicMock(return_value=rows)
    service._normalize = MagicMock(
        side_effect=lambda row: {
            "content_type": row.content_type,
            "content_id": "645757c6-d3bd-4dfa-a6a0-adabf9e640fc",
            "series_name": row.series_name or "The Rookie (2018)",
            "series_provider_id": "354311",
            "season_number": row.season_number,
            "episode_number": row.episode_number,
            "position_ms": row.position_ms,
            "duration_ms": row.duration_ms,
            "last_watched_at": row.last_watched_at,
            "progress_percent": 20,
        }
    )

    items = service.get_continue_watching("user-1", limit=20)

    assert len(items) == 1
    assert items[0]["season_number"] == 5
    assert items[0]["episode_number"] == 19


def test_continue_watching_keeps_multiple_distinct_movies():
    rows = [
        make_progress_row(content_id="movie:111", position_ms=300_000, duration_ms=3_000_000),
        make_progress_row(content_id="movie:222", position_ms=400_000, duration_ms=4_000_000),
    ]
    service = WatchProgressServiceV2(MagicMock())
    service.wp_repo.get_continue_watching = MagicMock(return_value=rows)
    service._normalize = MagicMock(
        side_effect=lambda row: {
            "content_type": "movie",
            "content_id": row.content_id,
            "series_name": None,
            "series_provider_id": "",
            "season_number": None,
            "episode_number": None,
            "position_ms": row.position_ms,
            "duration_ms": row.duration_ms,
            "last_watched_at": None,
            "progress_percent": 10,
        }
    )

    items = service.get_continue_watching("user-1", limit=20)

    assert len(items) == 2


def test_continue_watching_series_uses_catalog_title_when_serie_name_missing():
    """El catálogo real expone `title`, no `serie_name`; la serie debe resolverse igual."""
    progress_row = make_progress_row(
        content_id="series:645757c6",
        content_type="series",
        position_ms=120_000,
        duration_ms=1_200_000,
        title="Old episode",
        image_url="",
        series_name=None,
        season_number=None,
        episode_number=None,
    )
    series_meta = {
        "content_type": "series",
        "id": "645757c6-d3bd-4dfa-a6a0-adabf9e640fc",
        "provider_id": "354311",
        "title": "The Rookie (2018)",
        "logo": "",
    }

    service = make_service_with_mocks(progress_row, series_meta=series_meta)

    item = service.get_continue_watching("user-1", limit=20)[0]

    assert item["series_name"] == "The Rookie (2018)"
    assert item["series_provider_id"] == "354311"


def test_set_is_watched_with_season_episode_calls_repo_with_params():
    """mark-watched with season/episode should call repo.mark_watched with those params."""
    service = WatchProgressServiceV2(MagicMock())
    service.wp_repo.mark_watched = MagicMock(return_value=True)
    service._canonical_content_id = MagicMock(return_value="series:777")

    result = service.set_is_watched("user-1", "series:777", True, season=1, episode=2)

    assert result is True
    service._canonical_content_id.assert_called_once_with("series", "series:777")
    service.wp_repo.mark_watched.assert_called_once_with(
        "user-1", "series:777", True, season=1, episode=2, content_type="series"
    )


def test_natural_completion_triggers_preference_cleanup():
    service = WatchProgressServiceV2(MagicMock())
    service.wp_repo.mark_watched = MagicMock(return_value=True)
    service._canonical_content_id = MagicMock(return_value="catalog-id")
    service._delete_completed_playback_preference = MagicMock()

    result = service.set_is_watched(
        "user-1",
        "series:777",
        True,
        season=1,
        episode=2,
        completed=True,
    )

    assert result is True
    service._delete_completed_playback_preference.assert_called_once_with(
        "user-1", "series", "catalog-id"
    )


def test_set_is_watched_without_season_episode_uses_lookup_rows():
    """mark-watched without season/episode should use _lookup_rows (backward compat for movies)."""
    progress_row = make_progress_row()
    service = WatchProgressServiceV2(MagicMock())
    service._lookup_rows = MagicMock(return_value=[progress_row])
    service.wp_repo.mark_watched = MagicMock(return_value=True)

    result = service.set_is_watched("user-1", "movie:2053693", True)

    assert result is True
    service._lookup_rows.assert_called_once_with("user-1", "movie:2053693")
    service.wp_repo.mark_watched.assert_called_once_with(
        "user-1", progress_row.content_id, True, content_type="movie"
    )


def test_get_watched_items_forwards_limit_and_offset_and_returns_real_total():
    """get_watched_items debe paginar con offset y reportar el total real, no el tamaño de página."""
    row_a = make_progress_row(content_id="movie:111", is_watched=True)
    row_b = make_progress_row(content_id="movie:222", is_watched=True)
    service = WatchProgressServiceV2(MagicMock())
    service.wp_repo.get_watched_items = MagicMock(return_value=[row_a, row_b])
    service.wp_repo.count_watched_items = MagicMock(return_value=7)
    service._normalize = MagicMock(
        side_effect=lambda row: {
            "content_type": "movie",
            "content_id": row.content_id,
            "is_watched": row.is_watched,
        }
    )

    result = service.get_watched_items("user-1", limit=2, offset=4)

    service.wp_repo.get_watched_items.assert_called_once_with("user-1", 2, 4)
    service.wp_repo.count_watched_items.assert_called_once_with("user-1")
    assert result == {
        "items": [
            {"content_type": "movie", "content_id": "movie:111", "is_watched": True},
            {"content_type": "movie", "content_id": "movie:222", "is_watched": True},
        ],
        "total": 7,
    }
