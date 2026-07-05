"""Watch Progress Service v2 — uses SQLAlchemy repositories."""

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.repositories.content_repo import ContentRepository
from app.repositories.series_repo import SeriesRepository
from app.repositories.watch_progress_repo import WatchProgressRepository

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
DEFAULT_IMAGE_MOVIE = "/assets/images/movies.png"
DEFAULT_IMAGE_SERIES = "/assets/images/series.png"


class WatchProgressServiceV2:
    def __init__(self, session: Session):
        self.session = session
        self.wp_repo = WatchProgressRepository(session)
        self.content_repo = ContentRepository(session)
        self.series_repo = SeriesRepository(session)

    def get_continue_watching(self, user_id: str, limit: int = 20) -> list[dict]:

        rows = self.wp_repo.get_continue_watching(user_id, limit * 3)
        if not rows:
            return []

        incomplete: list[dict] = []
        for item in rows:
            duration = item.duration_ms or 0
            position = item.position_ms or 0
            if duration <= 0:
                continue
            progress = position / duration
            if progress < 0.95:
                incomplete.append(self._normalize(item))
                if len(incomplete) >= limit:
                    break
        return incomplete

    def get_watched_items(self, user_id: str, limit: int = 100) -> list[dict]:
        rows = self.wp_repo.get_watched_items(user_id, limit)
        return [self._normalize(r) for r in rows]

    def get_progress(self, user_id: str, content_id: str) -> dict | None:
        rows = self._lookup_rows(user_id, content_id)
        if not rows:
            return None
        return self._normalize(rows[0])

    def upsert_progress(self, user_id: str, content_id: str, data: dict) -> dict:
        canonical = self._canonical_content_id(data.get("content_type"), content_id)
        payload = {
            "content_type": data["content_type"],
            "position_ms": data["position_ms"],
            "duration_ms": data["duration_ms"],
            "series_name": data.get("series_name"),
            "season_number": data.get("season_number"),
            "episode_number": data.get("episode_number"),
            "title": data.get("title", ""),
            "image_url": data.get("image_url", ""),
            "last_watched_at": datetime.now(UTC),
        }
        row = self.wp_repo.upsert(user_id, canonical, payload)
        duration_ms = row.duration_ms or 0
        position_ms = row.position_ms or 0
        if duration_ms > 0 and (position_ms / duration_ms) >= 0.95 and not row.is_watched:
            row.is_watched = True
            self.session.flush()
        return self._normalize(row)

    def delete_progress(self, user_id: str, content_id: str) -> bool:
        return self.wp_repo.delete_by_user_and_content_id(user_id, content_id)

    def delete_episode_progress(
        self, user_id: str, content_id: str, season: int, episode: int
    ) -> bool:
        return self.wp_repo.delete_episode(user_id, content_id, season, episode)

    def set_is_watched(self, user_id: str, content_id: str, is_watched: bool, season: int | None = None, episode: int | None = None) -> bool:
        if season is not None or episode is not None:
            canonical = self._canonical_content_id(None, content_id)
            return self.wp_repo.mark_watched(user_id, canonical, is_watched, season=season, episode=episode) is not None
        rows = self._lookup_rows(user_id, content_id)
        if not rows:
            canonical = self._canonical_content_id(None, content_id)
            return self.wp_repo.mark_watched(user_id, canonical, is_watched) is not None
        result = False
        for row in rows:
            if self.wp_repo.mark_watched(user_id, row.content_id, is_watched):
                result = True
        return result

    def is_series_complete(self, user_id: str, series_name: str) -> bool:
        last = self.wp_repo.get_series_last_episode(user_id, series_name)
        return last is not None and bool(last.is_watched)

    def _normalize(self, row) -> dict:
        content_type = row.content_type
        content_row = self._find_content_row(content_type, row.content_id)
        canonical_id = self._lookup_id(row.content_id)

        normalized = {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "content_id": canonical_id,
            "content_type": content_type,
            "position_ms": row.position_ms,
            "duration_ms": row.duration_ms,
            "series_name": row.series_name,
            "season_number": row.season_number,
            "episode_number": row.episode_number,
            "title": row.title,
            "image_url": row.image_url,
            "last_watched_at": (row.last_watched_at.isoformat() if row.last_watched_at else None),
            "is_watched": row.is_watched,
        }

        if content_row:
            normalized["title"] = content_row.get("nombre") or row.title or ""
            normalized["normalized_title"] = (
                self._normalized_title(content_type, content_row) or normalized["title"]
            )
            normalized["image_url"] = self._image_url(content_row, row, content_type)
            self._apply_metadata(normalized, content_type, content_row)
            if content_type == "series":
                normalized["series_provider_id"] = str(content_row.get("provider_id") or "")
                normalized["series_name"] = content_row.get("serie_name") or row.series_name
                normalized["season_number"] = (
                    self._safe_int(content_row.get("temporada")) or row.season_number
                )
                normalized["episode_number"] = (
                    self._safe_int(content_row.get("episodio")) or row.episode_number
                )
                if not content_row.get("tmdb_id") and row.series_name:
                    twin = self.series_repo.find_canonical_by_title(row.series_name)
                    if twin and twin.get("tmdb_id"):
                        self._apply_metadata(normalized, content_type, twin)
        else:
            normalized["normalized_title"] = row.title or ""
            if content_type == "series" and row.series_name:
                twin = self.series_repo.find_canonical_by_title(row.series_name)
                if twin:
                    canonical_id = str(twin.get("id") or canonical_id)
                    normalized["content_id"] = canonical_id
                    normalized["series_provider_id"] = str(twin.get("provider_id") or "")
                    self._apply_metadata(normalized, content_type, twin)
                    normalized["series_name"] = twin.get("serie_name") or row.series_name

        position = row.position_ms or 0
        duration = row.duration_ms or 0
        normalized["progress_percent"] = int(position / duration * 100) if duration > 0 else 0
        normalized["is_watched"] = row.is_watched

        return normalized

    def _normalized_title(self, content_type: str | None, content_row: dict) -> str:
        if content_type == "movie":
            return str(content_row.get("nombre_normalizado") or content_row.get("nombre") or "")
        if content_type == "series":
            return str(
                content_row.get("serie_name")
                or content_row.get("nombre_normalizado")
                or content_row.get("nombre")
                or ""
            )
        return str(content_row.get("nombre_normalizado") or content_row.get("nombre") or "")

    def _apply_metadata(
        self, normalized: dict, content_type: str | None, content_row: dict
    ) -> None:
        if content_type not in ("movie", "series"):
            return
        overview = content_row.get("overview_es") or content_row.get("overview_en")
        normalized["overview"] = overview
        normalized["overview_es"] = content_row.get("overview_es")
        normalized["overview_en"] = content_row.get("overview_en")
        normalized["rating"] = content_row.get("vote_average")
        normalized["vote_average"] = content_row.get("vote_average")
        normalized["vote_count"] = content_row.get("vote_count")
        normalized["genres"] = content_row.get("genres")
        normalized["poster_path"] = content_row.get("poster_path") or content_row.get(
            "tmdb_poster_path"
        )
        normalized["backdrop_path"] = content_row.get("backdrop_path")
        normalized["runtime_minutes"] = content_row.get("runtime_minutes")
        normalized["tagline"] = content_row.get("tagline")
        normalized["release_date"] = content_row.get("release_date")
        normalized["year"] = content_row.get("year")
        normalized["tmdb_id"] = content_row.get("tmdb_id")
        normalized["tmdb_title"] = content_row.get("tmdb_title")
        normalized["popularity"] = content_row.get("popularity")
        normalized["status"] = content_row.get("status")
        if content_type == "series":
            normalized["total_seasons"] = content_row.get("total_seasons")

    def _image_url(self, content_row: dict, progress_row, content_type: str | None = None) -> str:
        default_map = {
            "movie": DEFAULT_IMAGE_MOVIE,
            "series": DEFAULT_IMAGE_SERIES,
        }
        default_img = default_map.get(content_type or "", DEFAULT_IMAGE_MOVIE)
        logo = str(content_row.get("logo") or "")
        poster_path = content_row.get("poster_path") or content_row.get("tmdb_poster_path")
        if self._is_placeholder(logo):
            return (
                self._build_tmdb_url(poster_path) or logo or progress_row.image_url or default_img
            )
        return logo or self._build_tmdb_url(poster_path) or progress_row.image_url or default_img

    @staticmethod
    def _build_tmdb_url(path: str | None, size: str = "w500") -> str:
        if not path:
            return ""
        path_str = str(path)
        if path_str.startswith("http://") or path_str.startswith("https://"):
            return path_str
        if not path_str.startswith("/"):
            path_str = f"/{path_str}"
        return f"{TMDB_IMAGE_BASE_URL}/{size}{path_str}"

    @staticmethod
    def _is_placeholder(url: str) -> bool:
        if not url:
            return True
        lower = url.lower()
        return "placeholder" in lower or "via.placeholder.com" in lower

    def _canonical_content_id(self, content_type: str | None, content_id: str) -> str:
        content_row = self._find_content_row(content_type, content_id)
        if content_row and content_row.get("id"):
            return str(content_row["id"])
        return self._lookup_id(content_id)

    def _lookup_rows(self, user_id: str, content_id: str) -> list:
        candidates = []
        for c in (
            content_id,
            self._lookup_id(content_id),
            f"movie:{self._lookup_id(content_id)}",
            f"series:{self._lookup_id(content_id)}",
        ):
            if c and c not in candidates:
                candidates.append(c)

        # Si el content_id original es UUID, la canonicalización pudo haber
        # guardado bajo un provider_id numérico — añadirlo como candidato
        for ct in ("movie", "series"):
            row = self._find_content_row(ct, content_id)
            if row and row.get("provider_id"):
                pid = str(row["provider_id"])
                if pid not in candidates:
                    candidates.append(pid)

        rows = []
        seen = set()
        for c in candidates:
            wp = self.wp_repo.get_by_user_and_content(user_id, c)
            if wp and str(wp.id) not in seen:
                rows.append(wp)
                seen.add(str(wp.id))
        return rows

    def _find_content_row(self, content_type: str | None, content_id: str | None) -> dict | None:
        if not content_type or not content_id:
            return None
        lookup_id = self._lookup_id(content_id)

        if content_type == "movie":
            result = self.content_repo.get_movie_with_metadata(lookup_id)
            if result:
                return result
            result = self.content_repo.search_by_provider_id(lookup_id)
            if result:
                return {"provider_id": result.provider_id, "nombre": result.title}
        elif content_type == "series":
            result = self.series_repo.get_with_metadata(lookup_id)
            if result:
                return result
            result = self.series_repo.search_by_provider_id(lookup_id)
            if result:
                return {"provider_id": result.provider_id, "nombre": result.title}
            result = self.series_repo.get_catalog_by_episode_provider_id(lookup_id)
            if result:
                return result
            result = self.series_repo.get_catalog_by_episode_id(lookup_id)
            if result:
                return result

        return None

    @staticmethod
    def _lookup_id(content_id: str | None) -> str:
        value = str(content_id or "")
        return value.split(":", 1)[1] if ":" in value else value

    @staticmethod
    def _safe_int(value) -> int | None:
        try:
            return int(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None
