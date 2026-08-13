"""Consulta en tiempo real resultados de Torrentio sin persistir torrents."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from iptv_api.core.config import get_settings

logger = logging.getLogger("iptv-api.torrentio")

_IMDB_PATTERN = re.compile(r"^tt\d+$", re.IGNORECASE)
_SEEDERS_PATTERN = re.compile(r"[👤]\s*([\d,.]+)")
_SIZE_PATTERN = re.compile(r"💾\s*([\d,.]+)\s*(KB|MB|GB|TB)", re.IGNORECASE)
_LANGUAGE_CODES = {"🇪🇸": "ES", "🇬🇧": "EN", "🇯🇵": "JP"}
_EXCLUDED_LANGUAGE_MARKERS = ("🇲🇽", "latino")
_QUALITY_PATTERN = re.compile(r"\b(4k|2160p|1080p|720p|480p)\b", re.IGNORECASE)


@dataclass(frozen=True)
class _CacheEntry:
    expires_at: float
    items: list[dict[str, Any]]


class TorrentioService:
    """Cliente de Torrentio con cache efimera en memoria."""

    _cache: dict[str, _CacheEntry] = {}

    def __init__(self, session: requests.Session | None = None) -> None:
        settings = get_settings()
        self.base_url = settings.torrentio_base_url.rstrip("/")
        self.providers = settings.torrentio_providers
        self.languages = settings.torrentio_languages
        self.timeout = settings.torrentio_timeout_seconds
        self.cache_ttl = settings.torrentio_cache_ttl_seconds
        self.proxy = settings.torrentio_proxy
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "WalacTV-API/Torrentio",
            }
        )

    def get_movie_streams(self, imdb_id: str) -> list[dict[str, Any]]:
        """Busca streams para una pelicula por IMDb."""
        self._validate_imdb_id(imdb_id)
        return self._get_streams("movie", imdb_id)

    def get_episode_streams(self, imdb_id: str, season: int, episode: int) -> list[dict[str, Any]]:
        """Busca streams para un episodio por IMDb, temporada y episodio."""
        self._validate_imdb_id(imdb_id)
        if season < 0 or episode < 0:
            raise ValueError("season y episode deben ser positivos")
        return self._get_streams("series", f"{imdb_id}:{season}:{episode}")

    def _get_streams(self, content_type: str, content_id: str) -> list[dict[str, Any]]:
        config = self._configuration_path()
        cache_key = f"{config}/{content_type}/{content_id}"
        cached = self._cache.get(cache_key)
        now = time.monotonic()
        if cached and cached.expires_at > now:
            return [dict(item) for item in cached.items]

        encoded_config = quote(config, safe="=,")
        url = f"{self.base_url}/{encoded_config}/stream/{content_type}/{content_id}.json"
        request_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.proxy:
            request_kwargs["proxies"] = {"http": self.proxy, "https": self.proxy}
        response = self.session.get(url, **request_kwargs)
        response.raise_for_status()
        payload = response.json()
        raw_streams = payload.get("streams", []) if isinstance(payload, dict) else []
        if not isinstance(raw_streams, list):
            raise ValueError("Torrentio devolvio una respuesta invalida")

        items = [
            item
            for raw in raw_streams
            if isinstance(raw, dict)
            for item in [self._normalize_stream(raw)]
            if item is not None
        ]
        self._cache[cache_key] = _CacheEntry(now + self.cache_ttl, items)
        self._trim_cache(now)
        return [dict(item) for item in items]

    def _configuration_path(self) -> str:
        parts = []
        if self.providers:
            parts.append(f"providers={quote(self.providers, safe=',')}")
        if self.languages:
            parts.append(f"language={quote(self.languages, safe=',')}")
        return "|".join(parts)

    @classmethod
    def _normalize_stream(cls, raw: dict[str, Any]) -> dict[str, Any] | None:
        info_hash = str(raw.get("infoHash") or "").strip()
        if not re.fullmatch(r"[a-fA-F0-9]{40}", info_hash):
            return None

        title = str(raw.get("title") or "").strip()
        language = cls._detect_language(title)
        if language is None:
            return None

        name = str(raw.get("name") or "").strip()
        quality_match = _QUALITY_PATTERN.search(name) or _QUALITY_PATTERN.search(title)
        size_match = _SIZE_PATTERN.search(title)
        return {
            "url": "",
            "label": cls._provider_label(title),
            "country": language,
            "quality": quality_match.group(1).upper() if quality_match else None,
            "provider_id": info_hash,
            "source": "torrentio",
            "provider": cls._provider_label(title),
            "language": language,
            "playable": False,
            "requires_resolution": True,
            "info_hash": info_hash,
            "file_idx": raw.get("fileIdx"),
            "seeders": cls._parse_seeders(title),
            "size_bytes": cls._parse_size_bytes(size_match),
            "title": title,
        }

    @staticmethod
    def _detect_language(title: str) -> str | None:
        lowered = title.lower()
        if any(marker in lowered for marker in _EXCLUDED_LANGUAGE_MARKERS):
            return None
        explicit = [marker for marker in _LANGUAGE_CODES if marker in title]
        foreign_flags = any(
            marker in title for marker in ("🇮🇹", "🇵🇹", "🇷🇺", "🇫🇷", "🇩🇪", "🇵🇱", "🇨🇳", "🇯🇵")
        )
        if foreign_flags and not any(marker in title for marker in _LANGUAGE_CODES):
            return None
        if "🇪🇸" in title:
            return "ES"
        if "🇬🇧" in title:
            return "EN"
        if "🇯🇵" in title or re.search(r"\b(japanese|japonesa?|japon(?:es|és)?)\b", lowered):
            return "JP"
        if "日本語" in title or "日本" in title:
            return "JP"
        if re.search(r"\b(spanish|castellano)\b", lowered):
            return "ES"
        if re.search(r"\benglish\b", lowered):
            return "EN"
        if explicit:
            return next(_LANGUAGE_CODES[marker] for marker in explicit)
        return "EN"

    @staticmethod
    def _provider_label(title: str) -> str:
        marker = "⚙️"
        return title.split(marker, 1)[1].strip().splitlines()[0] if marker in title else "Torrentio"

    @staticmethod
    def _parse_seeders(title: str) -> int | None:
        match = _SEEDERS_PATTERN.search(title)
        if not match:
            return None
        try:
            return int(match.group(1).replace(",", "").replace(".", ""))
        except ValueError:
            return None

    @staticmethod
    def _parse_size_bytes(match: re.Match[str] | None) -> int | None:
        if not match:
            return None
        try:
            value = float(match.group(1).replace(",", "."))
        except ValueError:
            return None
        multiplier = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
        return int(value * multiplier[match.group(2).upper()])

    @classmethod
    def _trim_cache(cls, now: float) -> None:
        cls._cache = {key: entry for key, entry in cls._cache.items() if entry.expires_at > now}

    @staticmethod
    def _validate_imdb_id(imdb_id: str) -> None:
        if not _IMDB_PATTERN.fullmatch(imdb_id):
            raise ValueError("imdb_id debe tener formato tt1234567")
