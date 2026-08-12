"""Watch Progress Service v2 — uses SQLAlchemy repositories."""

import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from iptv_api.repositories.content_repo import ContentRepository
from iptv_api.repositories.playback_preference_repo import PlaybackPreferenceRepository
from iptv_api.repositories.series_repo import SeriesRepository
from iptv_api.repositories.watch_progress_repo import WatchProgressRepository

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
        self.playback_preference_repo = PlaybackPreferenceRepository(session)

    def get_continue_watching(self, user_id: str, limit: int = 20) -> list[dict]:

        rows = self.wp_repo.get_continue_watching(user_id, limit * 5)
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

        return self._dedupe_by_series(incomplete)[:limit]

    def get_continue_watching_home(self, user_id: str, limit: int = 20) -> list[dict]:
        """Obtiene una entrada por película o serie para la pantalla de inicio."""
        active_rows = self.wp_repo.get_continue_watching(user_id, max(limit * 5, 100))
        watched_rows = self.wp_repo.get_watched_items(user_id, max(limit * 5, 100), 0)

        active = [
            self._normalize(row)
            for row in active_rows
            if self._has_resume_progress(row)
        ]
        watched = [self._normalize(row) for row in watched_rows]
        by_group: dict[str, dict] = {}

        for item in active:
            if item.get("content_type") == "movie":
                by_group.setdefault(self._series_group_key(item), item)
                continue
            key = self._series_group_key(item)
            current = by_group.get(key)
            if current is None or self._is_newer(item, current):
                by_group[key] = item

        for item in watched:
            if item.get("content_type") != "series":
                continue
            key = self._series_group_key(item)
            if key not in by_group:
                by_group[key] = item

        return sorted(
            by_group.values(),
            key=lambda item: item.get("last_watched_at") or "",
            reverse=True,
        )[:limit]

    @staticmethod
    def _has_resume_progress(row) -> bool:
        duration = row.duration_ms or 0
        position = row.position_ms or 0
        return duration > 0 and position > 0 and position / duration < 0.95 and not row.is_watched

    @staticmethod
    def _is_newer(new_item: dict, old_item: dict) -> bool:
        return (new_item.get("last_watched_at") or "") > (old_item.get("last_watched_at") or "")

    def _dedupe_by_series(self, items: list[dict]) -> list[dict]:
        """Collapsa varias filas de la misma serie a una sola entrada
        (el episodio más reciente con metadatos). Evita duplicados en
        Continuar viendo y stableIds repetidos en el cliente."""
        best: dict[str, dict] = {}
        for item in items:
            key = self._series_group_key(item)
            current = best.get(key)
            if current is None or self._is_better_cw_item(item, current):
                best[key] = item
        return list(best.values())

    @staticmethod
    def _series_group_key(item: dict) -> str:
        content_type = item.get("content_type")
        if content_type != "series":
            return f"movie:{item.get('content_id') or ''}"
        provider_id = item.get("series_provider_id")
        if provider_id:
            return f"series:provider:{provider_id}"
        content_id = item.get("content_id")
        if content_id:
            return f"series:content:{content_id}"
        series_name = (item.get("series_name") or "").strip().lower()
        if series_name:
            return f"series:name:{series_name}"
        return f"series:unknown:{content_type}"

    @staticmethod
    def _is_better_cw_item(new_item: dict, old_item: dict) -> bool:
        def score(item: dict) -> tuple:
            has_episode = (
                item.get("season_number") is not None and item.get("episode_number") is not None
            )
            return (has_episode, item.get("last_watched_at") or "")

        return score(new_item) > score(old_item)

    def get_watched_items(self, user_id: str, limit: int = 100, offset: int = 0) -> dict:
        rows = self.wp_repo.get_watched_items(user_id, limit, offset)
        total = self.wp_repo.count_watched_items(user_id)
        return {"items": [self._normalize(r) for r in rows], "total": total}

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

    def set_is_watched(
        self,
        user_id: str,
        content_id: str,
        is_watched: bool,
        season: int | None = None,
        episode: int | None = None,
        completed: bool = False,
    ) -> bool:
        content_type = self._extract_content_type_prefix(content_id)
        if season is not None or episode is not None:
            content_type = content_type or "series"
            canonical = self._canonical_content_id(content_type, content_id)
            result = (
                self.wp_repo.mark_watched(
                    user_id,
                    canonical,
                    is_watched,
                    season=season,
                    episode=episode,
                    content_type=content_type,
                )
                is not None
            )
            if result and is_watched and completed:
                self._delete_completed_playback_preference(user_id, content_type, canonical)
            return result
        rows = self._lookup_rows(user_id, content_id)
        if rows:
            content_type = content_type or rows[0].content_type
            result = False
            for row in rows:
                if self.wp_repo.mark_watched(
                    user_id, row.content_id, is_watched, content_type=row.content_type
                ):
                    result = True
            if result and is_watched and completed:
                self._delete_preference_for_identifier(user_id, content_type, content_id)
            return result
        if content_type is None:
            content_type = "movie" if self._find_content_row("movie", content_id) else "series"
        canonical = self._canonical_content_id(content_type, content_id)
        result = (
            self.wp_repo.mark_watched(user_id, canonical, is_watched, content_type=content_type)
            is not None
        )
        if result and is_watched and completed:
            self._delete_completed_playback_preference(user_id, content_type, canonical)
        return result

    def _delete_preference_for_identifier(
        self, user_id: str, content_type: str, content_id: str
    ) -> None:
        try:
            row = self._find_content_row(content_type, content_id)
            if row and row.get("id"):
                self._delete_completed_playback_preference(user_id, content_type, str(row["id"]))
        except Exception:
            return

    def _delete_completed_playback_preference(
        self, user_id: str, content_type: str, catalog_id: str
    ) -> None:
        try:
            canonical_uuid = UUID(catalog_id)
        except ValueError:
            return
        if content_type == "movie":
            self.playback_preference_repo.delete_for_content(user_id, "movie", canonical_uuid)
            return
        if content_type != "series":
            return
        counts = self.series_repo._get_episode_counts(canonical_uuid)
        total_episodes = int(counts.get("total_episodes") or 0)
        if total_episodes <= 0:
            return
        rows = self.wp_repo.get_all_for_user_and_series(user_id, catalog_id=catalog_id)
        watched_episodes = {
            (row.season_number, row.episode_number)
            for row in rows
            if row.is_watched and row.season_number is not None and row.episode_number is not None
        }
        if len(watched_episodes) >= total_episodes:
            self.playback_preference_repo.delete_for_content(user_id, "series", canonical_uuid)

    @staticmethod
    def _extract_content_type_prefix(content_id: str) -> str | None:
        if ":" in content_id:
            prefix = content_id.split(":", 1)[0]
            if prefix in ("movie", "series"):
                return prefix
        return None

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
            if content_type == "movie":
                normalized["provider_id"] = str(content_row.get("provider_id") or "")
            if content_type == "series":
                normalized["series_provider_id"] = str(content_row.get("provider_id") or "")
                normalized["series_name"] = (
                    content_row.get("serie_name") or content_row.get("title") or row.series_name
                )
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
