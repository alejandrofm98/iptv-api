"""Sinopsis en espanol bajo demanda desde TMDB para fichas de Cinemeta."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from iptv_api.core.config import get_settings

logger = logging.getLogger("iptv-api.tmdb-es")


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    item: dict[str, Any] | None


class TmdbEsService:
    """Traduce una ficha (moviedb_id de Cinemeta) a titulo/sinopsis es-ES.

    Solo cubre el hueco que Cinemeta no da (espanol). Si no hay
    TMDB_API_KEY ni TMDB_READ_TOKEN configurados, devuelve None para
    que el llamador use la descripcion inglesa de Cinemeta.
    """

    _cache: dict[str, _CacheEntry] = {}

    def __init__(self, session: requests.Session | None = None) -> None:
        settings = get_settings()
        self.base_url = settings.tmdb_base_url.rstrip("/")
        self.api_key = settings.tmdb_api_key.strip()
        self.read_token = settings.tmdb_read_token.strip()
        self.timeout = settings.tmdb_es_timeout_seconds
        self.cache_ttl = settings.tmdb_es_cache_ttl_seconds
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "WalacTV-API/TmdbEs",
            }
        )
        if self.read_token:
            self.session.headers["Authorization"] = f"Bearer {self.read_token}"

    @property
    def is_configured(self) -> bool:
        """Indica si hay credenciales TMDB para consultar."""
        return bool(self.api_key or self.read_token)

    def get_spanish(self, moviedb_id: int, content_type: str) -> dict[str, Any] | None:
        """Devuelve titulo/sinopsis es-ES o None si no hay traduccion."""
        if content_type not in ("movie", "series"):
            raise ValueError("content_type debe ser movie o series")
        if moviedb_id <= 0:
            raise ValueError("moviedb_id debe ser positivo")
        if not self.is_configured:
            return None
        cache_key = f"{content_type}/{moviedb_id}"
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and cached.expires_at > now:
            return dict(cached.item) if cached.item is not None else None

        resource = "movie" if content_type == "movie" else "tv"
        url = f"{self.base_url}/{resource}/{moviedb_id}"
        params: dict[str, Any] = {"language": "es-ES"}
        if not self.read_token:
            params["api_key"] = self.api_key
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        item = self._normalize(payload if isinstance(payload, dict) else {})
        self._cache[cache_key] = _CacheEntry(now + self.cache_ttl, item)
        self._trim_cache(now)
        return dict(item) if item is not None else None

    @staticmethod
    def _normalize(payload: dict[str, Any]) -> dict[str, Any] | None:
        title = payload.get("title") or payload.get("name")
        overview = payload.get("overview")
        title_es = str(title).strip() if title else None
        overview_es = str(overview).strip() if overview else None
        if not title_es and not overview_es:
            return None
        return {"title_es": title_es, "overview_es": overview_es}

    @classmethod
    def _trim_cache(cls, now: float) -> None:
        cls._cache = {key: entry for key, entry in cls._cache.items() if entry.expires_at > now}
