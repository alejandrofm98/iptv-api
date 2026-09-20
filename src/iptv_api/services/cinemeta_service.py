"""Cliente read-only del addon Cinemeta de Stremio para metadata de catalogo."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

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

    def get_catalog(
        self,
        content_type: str,
        catalog_id: str = "top",
        skip: int = 0,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """Devuelve un catálogo Cinemeta normalizado, opcionalmente filtrado por texto."""
        if content_type not in ("movie", "series"):
            raise ValueError("content_type debe ser movie o series")
        if not catalog_id or not re.fullmatch(r"[A-Za-z0-9_-]+", catalog_id):
            raise ValueError("catalog_id no es válido")
        if skip < 0:
            raise ValueError("skip no puede ser negativo")
        normalized_search = search.strip() if search else ""
        if len(normalized_search) > 120:
            raise ValueError("search no puede superar 120 caracteres")

        cache_key = f"catalog/{content_type}/{catalog_id}/{skip}/{normalized_search.lower()}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > now:
            return [dict(item) for item in cached.meta.get("items", [])]

        path = f"/catalog/{content_type}/{catalog_id}"
        if normalized_search:
            path += f"/search={quote(normalized_search, safe='')}"
        if skip:
            path += f"/skip={skip}"
        response = self.session.get(f"{self.base_url}{path}.json", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        raw_items = payload.get("metas") if isinstance(payload, dict) else None
        if not isinstance(raw_items, list):
            raise ValueError("Cinemeta devolvió un catálogo inválido")
        items = [
            self._normalize_catalog_item(item, content_type)
            for item in raw_items
            if isinstance(item, dict) and (item.get("id") or item.get("imdb_id"))
        ]
        self._cache[cache_key] = _CacheEntry(now + self.cache_ttl, {"items": items})
        self._trim_cache(now)
        return [dict(item) for item in items]

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

    @staticmethod
    def _normalize_catalog_item(raw: dict[str, Any], content_type: str) -> dict[str, Any]:
        item_id = str(raw.get("imdb_id") or raw.get("id") or "")
        release_info = str(raw.get("releaseInfo") or raw.get("year") or "")
        year_match = re.search(r"\b(19|20)\d{2}\b", release_info)
        rating = raw.get("imdbRating") or raw.get("rating")
        try:
            rating_value = float(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating_value = None
        return {
            "id": item_id,
            "title": raw.get("name") or raw.get("title"),
            "type": content_type,
            "description": raw.get("description") or raw.get("overview"),
            "poster": raw.get("poster"),
            "backdrop": raw.get("background") or raw.get("backdrop"),
            "logo": raw.get("logo"),
            "genres": _as_str_list(raw.get("genres") or raw.get("genre")),
            "rating": rating_value,
            "year": int(year_match.group(0)) if year_match else None,
            "imdb_id": item_id if _IMDB_PATTERN.fullmatch(item_id) else None,
        }

    @classmethod
    def _trim_cache(cls, now: float) -> None:
        cls._cache = {key: entry for key, entry in cls._cache.items() if entry.expires_at > now}

    @staticmethod
    def _validate_imdb_id(imdb_id: str) -> None:
        if not _IMDB_PATTERN.fullmatch(imdb_id):
            raise ValueError("imdb_id debe tener formato tt1234567")
