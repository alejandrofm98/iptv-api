from services.watch_progress_service import WatchProgressService


class FakePostgresService:
    def __init__(self, content_row: dict | None):
        self.content_row = content_row

    def get_continue_watching(self, user_id: str, limit: int) -> list[dict]:
        return [
            {
                "user_id": user_id,
                "content_id": "movie:2053693",
                "content_type": "movie",
                "position_ms": 300_000,
                "duration_ms": 3_000_000,
                "title": "Old title",
                "image_url": "https://old/img.jpg",
                "last_watched_at": "2026-05-12T10:00:00Z",
                "is_watched": False,
            }
        ]

    def get_movie_with_metadata(self, movie_id: str) -> dict | None:
        if self.content_row and self.content_row.get("content_type") == "movie":
            return self.content_row
        return None

    def get_series_with_metadata(self, series_id: str) -> dict | None:
        if self.content_row and self.content_row.get("content_type") == "series":
            return self.content_row
        return None

    def get_content_item_by_provider_id(self, table: str, value: str) -> dict | None:
        return None

    def get_content_item_by_id(self, table: str, value: str) -> dict | None:
        return None


def test_continue_watching_movie_includes_tmdb_metadata_and_poster_for_placeholder_logo():
    service = WatchProgressService(
        FakePostgresService(
            {
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
        )
    )

    item = service.get_continue_watching("user-1", limit=20)[0]

    assert item["content_id"] == "2053693"
    assert item["title"] == "ES - Los colores del tiempo (LQ) (2025)"
    assert item["normalized_title"] == "Los colores del tiempo (2025)"
    assert item["image_url"] == "https://image.tmdb.org/t/p/w500/poster.jpg"
    assert item["overview"] == "Descripcion ES"
    assert item["overview_en"] == "Description EN"
    assert item["rating"] == 7.4
    assert item["vote_average"] == 7.4
    assert item["poster_path"] == "/poster.jpg"
    assert item["backdrop_path"] == "/backdrop.jpg"
    assert item["runtime_minutes"] == 124
    assert item["tmdb_title"] == "Los colores del tiempo"
    assert item["progress_percent"] == 10


def test_continue_watching_keeps_real_logo_when_available():
    service = WatchProgressService(
        FakePostgresService(
            {
                "content_type": "movie",
                "id": "movie-row-id",
                "provider_id": "2053693",
                "nombre": "Movie title",
                "nombre_normalizado": "Movie title",
                "logo": "https://cdn.test/movie.jpg",
                "tmdb_poster_path": "/poster.jpg",
            }
        )
    )

    item = service.get_continue_watching("user-1", limit=20)[0]

    assert item["image_url"] == "https://cdn.test/movie.jpg"


def test_continue_watching_series_includes_tmdb_metadata():
    class SeriesPostgresService(FakePostgresService):
        def get_continue_watching(self, user_id: str, limit: int) -> list[dict]:
            return [
                {
                    "user_id": user_id,
                    "content_id": "series:777",
                    "content_type": "series",
                    "position_ms": 120_000,
                    "duration_ms": 1_200_000,
                    "series_name": "Old Serie",
                    "season_number": 1,
                    "episode_number": 1,
                    "title": "Old episode",
                    "image_url": "",
                    "is_watched": False,
                }
            ]

    service = WatchProgressService(
        SeriesPostgresService(
            {
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
        )
    )

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
