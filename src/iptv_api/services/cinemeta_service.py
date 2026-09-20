"""Cliente read-only del addon Cinemeta de Stremio para metadata de catalogo."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import requests

from iptv_api.core.config import get_settings

logger = logging.getLogger("iptv-api.cinemeta")

_IMDB_PATTERN = re.compile(r"^tt\d+$", re.IGNORECASE)


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    meta: dict[str, Any]


class CinemetaService:
    """Consulta fichas de Cinemeta sin persistir nada en PostgreSQL."""

    _cache: dict[str, _CacheEntry] = {}

    def __init__(self, session: requests.Session | None = None) -> None:
        settings = get_settings()
        self.base_url = settings.cinemeta_base_url.rstrip("/")
        self.timeout = settings.cinemeta_timeout_seconds
        self.cache_ttl = settings.cinemeta_cache_ttl_seconds
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "WalacTV-API/Cinemeta",
            }
        )

    def get_meta(self, content_type: str, imdb_id: str) -> dict[str, Any]:
        """Devuelve la ficha normalizada de una pelicula o serie."""
        if content_type not in ("movie", "series"):
            raise ValueError("content_type debe ser movie o series")
        self._validate_imdb_id(imdb_id)
        cache_key = f"{content_type}/{imdb_id.lower()}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > now:
            return dict(cached.meta)

        url = f"{self.base_url}/meta/{content_type}/{imdb_id}.json"
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        raw = payload.get("meta") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            raise ValueError("Cinemeta devolvio una respuesta invalida")
        meta = self._normalize(raw, content_type)
        self._cache[cache_key] = _CacheEntry(now + self.cache_ttl, meta)
        self._trim_cache(now)
        return dict(meta)

    @staticmethod
    def _normalize(raw: dict[str, Any], content_type: str) -> dict[str, Any]:
        raw_videos = raw.get("videos")
        videos: list[Any] = raw_videos if isinstance(raw_videos, list) else []
        episodes = [
            {
                "id": str(video.get("id") or ""),
                "season": video.get("season"),
                "episode": video.get("episode", video.get("number")),
                "title": video.get("name") or video.get("title"),
                "overview": video.get("overview") or video.get("description"),
                "thumbnail": video.get("thumbnail"),
                "released": video.get("released") or video.get("firstAired"),
            }
            for video in videos
            if isinstance(video, dict) and video.get("id")
        ]
        seasons = sorted({ep["season"] for ep in episodes if isinstance(ep["season"], int)})
        moviedb_id = raw.get("moviedb_id")
        try:
            moviedb_id = int(moviedb_id) if moviedb_id is not None else None
        except (TypeError, ValueError):
            moviedb_id = None
        return {
            "imdb_id": str(raw.get("imdb_id") or raw.get("id") or ""),
            "type": content_type,
            "name": raw.get("name"),
            "description_en": raw.get("description"),
            "year": raw.get("year"),
            "release_info": raw.get("releaseInfo"),
            "genres": _as_str_list(raw.get("genres") or raw.get("genre")),
            "cast": _as_str_list(raw.get("cast")),
            "director": _as_str_list(raw.get("director")),
            "runtime": raw.get("runtime"),
            "imdb_rating": raw.get("imdbRating"),
            "moviedb_id": moviedb_id,
            "poster": raw.get("poster"),
            "background": raw.get("background"),
            "logo": raw.get("logo"),
            "total_episodes": len(episodes),
            "seasons": seasons,
            "episodes": episodes,
        }

    @classmethod
    def _trim_cache(cls, now: float) -> None:
        cls._cache = {key: entry for key, entry in cls._cache.items() if entry.expires_at > now}

    @staticmethod
    def _validate_imdb_id(imdb_id: str) -> None:
        if not _IMDB_PATTERN.fullmatch(imdb_id):
            raise ValueError("imdb_id debe tener formato tt1234567")
