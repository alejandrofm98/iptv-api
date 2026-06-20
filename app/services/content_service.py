"""Content Service v2 — uses SQLAlchemy repositories."""

import gzip
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.repositories.channel_repo import ChannelRepository
from app.repositories.config_repo import SyncMetadataRepository
from app.repositories.content_repo import ContentRepository
from app.repositories.replay_repo import ReplayRepository
from app.repositories.series_repo import SeriesRepository
from utils.config import get_settings

logger = logging.getLogger("iptv-api")


def map_android_type(content_type: str) -> str:
    return {
        "channels": "channel",
        "movies": "movie",
        "series": "series",
        "events": "event",
    }.get(content_type, content_type)


class ContentServiceV2:
    TABLE_MAP = {"channels": "channels", "replays": "replays"}

    _cache: dict[str, tuple[Any, float]] = {}
    _CACHE_TTL_SECONDS = 300

    DEDUP_BUFFER_MULTIPLIER = 2.5
    MAX_FETCH_ROUNDS = 2

    REPLAY_EMBED_BASE_URL = "https://dailywrestling.cc/embed"
    REPLAY_METADATA_EMBEDDER = "https://dailywrestling.cc/"
    TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p"
    DEFAULT_IMAGE_MOVIE = "/assets/images/movies.png"
    DEFAULT_IMAGE_SERIES = "/assets/images/series.png"
    DEFAULT_IMAGE_CHANNEL = "/assets/images/channels.png"

    HOME_PAGE_SIZE = 24

    SECTION_PATTERNS: dict[str, list[dict[str, Any]]] = {
        "movies": [
            {"title": "2026 ESTRENOS", "year": 2026},
            {"title": "2025 ESTRENOS", "year": 2025},
            {"title": "PRIME", "group": "PRIME", "country": "ES"},
            {"title": "NETFLIX", "pattern": "NETFLIX"},
            {"title": "HBO MAX", "pattern": "HBO MAX"},
            {"title": "DISNEY+", "pattern": "DISNEY"},
        ],
        "series": [
            {"title": "PRIME", "group": "PRIME", "country": "ES"},
            {"title": "DISNEY+", "pattern": "DISNEY"},
            {"title": "NETFLIX", "pattern": "NETFLIX"},
            {"title": "HBO", "pattern": "HBO"},
        ],
    }

    COUNTRY_NAMES = {
        "AD": "Andorra",
        "AE": "Emiratos Árabes Unidos",
        "AF": "Afganistán",
        "AG": "Antigua y Barbuda",
        "AI": "Anguila",
        "AL": "Albania",
        "AM": "Armenia",
        "AO": "Angola",
        "AQ": "Antártida",
        "AR": "Argentina",
        "AS": "Samoa Americana",
        "AT": "Austria",
        "AU": "Australia",
        "AW": "Aruba",
        "AX": "Islas Åland",
        "AZ": "Azerbaiyán",
        "BA": "Bosnia y Herzegovina",
        "BB": "Barbados",
        "BD": "Bangladés",
        "BE": "Bélgica",
        "BF": "Burkina Faso",
        "BG": "Bulgaria",
        "BH": "Baréin",
        "BI": "Burundi",
        "BJ": "Benín",
        "BL": "San Bartolomé",
        "BM": "Bermudas",
        "BN": "Brunéi",
        "BO": "Bolivia",
        "BQ": "Caribe Neerlandés",
        "BR": "Brasil",
        "BS": "Bahamas",
        "BT": "Bután",
        "BW": "Botsuana",
        "BY": "Bielorrusia",
        "BZ": "Belice",
        "CA": "Canadá",
        "CC": "Islas Cocos",
        "CD": "República Democrática del Congo",
        "CF": "República Centroafricana",
        "CG": "República del Congo",
        "CH": "Suiza",
        "CI": "Costa de Marfil",
        "CK": "Islas Cook",
        "CL": "Chile",
        "CM": "Camerún",
        "CN": "China",
        "CO": "Colombia",
        "CR": "Costa Rica",
        "CU": "Cuba",
        "CV": "Cabo Verde",
        "CW": "Curazao",
        "CX": "Isla Christmas",
        "CY": "Chipre",
        "CZ": "Chequia",
        "DE": "Alemania",
        "DJ": "Yibuti",
        "DK": "Dinamarca",
        "DM": "Dominica",
        "DO": "República Dominicana",
        "DZ": "Argelia",
        "EC": "Ecuador",
        "EE": "Estonia",
        "EG": "Egipto",
        "EH": "Sáhara Occidental",
        "ER": "Eritrea",
        "ES": "España",
        "ET": "Etiopía",
        "FI": "Finlandia",
        "FJ": "Fiyi",
        "FK": "Islas Malvinas",
        "FM": "Micronesia",
        "FO": "Islas Feroe",
        "FR": "Francia",
        "GA": "Gabón",
        "GB": "Reino Unido",
        "GD": "Granada",
        "GE": "Georgia",
        "GF": "Guayana Francesa",
        "GG": "Guernsey",
        "GH": "Ghana",
        "GI": "Gibraltar",
        "GL": "Groenlandia",
        "GM": "Gambia",
        "GN": "Guinea",
        "GP": "Guadalupe",
        "GQ": "Guinea Ecuatorial",
        "GR": "Grecia",
        "GT": "Guatemala",
        "GU": "Guam",
        "GW": "Guinea-Bisáu",
        "GY": "Guyana",
        "HK": "Hong Kong",
        "HN": "Honduras",
        "HR": "Croacia",
        "HT": "Haití",
        "HU": "Hungría",
        "ID": "Indonesia",
        "IE": "Irlanda",
        "IL": "Israel",
        "IM": "Isla de Man",
        "IN": "India",
        "IO": "Territorio Británico del Océano Índico",
        "IQ": "Irak",
        "IR": "Irán",
        "IS": "Islandia",
        "IT": "Italia",
        "JE": "Jersey",
        "JM": "Jamaica",
        "JO": "Jordania",
        "JP": "Japón",
        "KE": "Kenia",
        "KG": "Kirguistán",
        "KH": "Camboya",
        "KI": "Kiribati",
        "KM": "Comoras",
        "KN": "San Cristóbal y Nieves",
        "KP": "Corea del Norte",
        "KR": "Corea del Sur",
        "KW": "Kuwait",
        "KY": "Islas Caimán",
        "KZ": "Kazajistán",
        "LA": "Laos",
        "LB": "Líbano",
        "LC": "Santa Lucía",
        "LI": "Liechtenstein",
        "LK": "Sri Lanka",
        "LR": "Liberia",
        "LS": "Lesoto",
        "LT": "Lituania",
        "LU": "Luxemburgo",
        "LV": "Letonia",
        "LY": "Libia",
        "MA": "Marruecos",
        "MC": "Mónaco",
        "MD": "Moldavia",
        "ME": "Montenegro",
        "MF": "San Martín",
        "MG": "Madagascar",
        "MH": "Islas Marshall",
        "MK": "Macedonia del Norte",
        "ML": "Malí",
        "MM": "Birmania",
        "MN": "Mongolia",
        "MO": "Macao",
        "MP": "Islas Marianas del Norte",
        "MQ": "Martinica",
        "MR": "Mauritania",
        "MS": "Montserrat",
        "MT": "Malta",
        "MU": "Mauricio",
        "MV": "Maldivas",
        "MW": "Malaui",
        "MX": "México",
        "MY": "Malasia",
        "MZ": "Mozambique",
        "NA": "Namibia",
        "NC": "Nueva Caledonia",
        "NE": "Níger",
        "NF": "Isla Norfolk",
        "NG": "Nigeria",
        "NI": "Nicaragua",
        "NL": "Países Bajos",
        "NO": "Noruega",
        "NP": "Nepal",
        "NR": "Nauru",
        "NU": "Niue",
        "NZ": "Nueva Zelanda",
        "OM": "Omán",
        "PA": "Panamá",
        "PE": "Perú",
        "PF": "Polinesia Francesa",
        "PG": "Papúa Nueva Guinea",
        "PH": "Filipinas",
        "PK": "Pakistán",
        "PL": "Polonia",
        "PM": "San Pedro y Miquelón",
        "PN": "Islas Pitcairn",
        "PR": "Puerto Rico",
        "PS": "Palestina",
        "PT": "Portugal",
        "PW": "Palaos",
        "PY": "Paraguay",
        "QA": "Catar",
        "RE": "Reunión",
        "RO": "Rumania",
        "RS": "Serbia",
        "RU": "Rusia",
        "RW": "Ruanda",
        "SA": "Arabia Saudita",
        "SB": "Islas Salomón",
        "SC": "Seychelles",
        "SD": "Sudán",
        "SE": "Suecia",
        "SG": "Singapur",
        "SH": "Santa Elena, Ascensión y Tristán de Acuña",
        "SI": "Eslovenia",
        "SJ": "Svalbard y Jan Mayen",
        "SK": "Eslovaquia",
        "SL": "Sierra Leona",
        "SM": "San Marino",
        "SN": "Senegal",
        "SO": "Somalia",
        "SR": "Surinam",
        "SS": "Sudán del Sur",
        "ST": "Santo Tomé y Príncipe",
        "SV": "El Salvador",
        "SX": "Sint Maarten",
        "SY": "Siria",
        "SZ": "Esuatini",
        "TC": "Islas Turcas y Caicos",
        "TD": "Chad",
        "TF": "Territorios Australes Franceses",
        "TG": "Togo",
        "TH": "Tailandia",
        "TJ": "Tayikistán",
        "TK": "Tokelau",
        "TL": "Timor Oriental",
        "TM": "Turkmenistán",
        "TN": "Túnez",
        "TO": "Tonga",
        "TR": "Turquía",
        "TT": "Trinidad y Tobago",
        "TV": "Tuvalu",
        "TW": "Taiwán",
        "TZ": "Tanzania",
        "UA": "Ucrania",
        "UG": "Uganda",
        "UM": "Islas Ultramarinas Menores de Estados Unidos",
        "US": "Estados Unidos",
        "UY": "Uruguay",
        "UZ": "Uzbekistán",
        "VA": "Ciudad del Vaticano",
        "VC": "San Vicente y las Granadinas",
        "VE": "Venezuela",
        "VG": "Islas Vírgenes Británicas",
        "VI": "Islas Vírgenes de los Estados Unidos",
        "VN": "Vietnam",
        "VU": "Vanuatu",
        "WF": "Wallis y Futuna",
        "WS": "Samoa",
        "YE": "Yemen",
        "YT": "Mayotte",
        "ZA": "Sudáfrica",
        "ZM": "Zambia",
        "ZW": "Zimbabue",
    }

    def __init__(self, session: Session):
        self.session = session
        self.content_repo = ContentRepository(session)
        self.series_repo = SeriesRepository(session)
        self.channel_repo = ChannelRepository(session)
        self.replay_repo = ReplayRepository(session)
        self.sync_meta_repo = SyncMetadataRepository(session)
        self.settings = get_settings()

    @classmethod
    def _get_cached(cls, key: str) -> Any | None:
        if key in cls._cache:
            value, cached_at = cls._cache[key]
            if time.time() - cached_at < cls._CACHE_TTL_SECONDS:
                return value
        return None

    @classmethod
    def _set_cached(cls, key: str, value: Any):
        cls._cache[key] = (value, time.time())

    @property
    def _https_base_url(self) -> str:
        return self.settings.public_domain.rstrip("/")

    def _extract_stream_id(self, url: str) -> tuple:
        if not url:
            return (None, None, "live")
        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split("/") if p]
        if len(path_parts) >= 2:
            path_lower = parsed.path.lower()
            if "/live/" in path_lower:
                content_type = "live"
            elif "/movie/" in path_lower:
                content_type = "movie"
            elif "/series/" in path_lower:
                content_type = "series"
            else:
                content_type = "live"
            last_part = path_parts[-1] if path_parts else ""
            if "." in last_part:
                parts = last_part.rsplit(".", 1)
                stream_id = parts[0]
                extension = parts[1] if len(parts) > 1 else "ts"
            else:
                stream_id = last_part
                extension = "ts"
            return (stream_id, extension, content_type)
        return (None, None, "live")

    def _build_proxy_url(self, original_url: str, username: str, password: str) -> str:
        if not original_url:
            return ""
        stream_id, extension, content_type = self._extract_stream_id(original_url)
        if not stream_id:
            return ""
        base_url = self._https_base_url
        if content_type == "live":
            return f"{base_url}/{username}/{password}/{stream_id}"
        if extension:
            return f"{base_url}/{content_type}/{username}/{password}/{stream_id}.{extension}"
        return f"{base_url}/{content_type}/{username}/{password}/{stream_id}"

    @staticmethod
    def _interpolate_stream_url_template(stream_url: str, username: str, password: str) -> str:
        if not stream_url:
            return stream_url
        if username:
            stream_url = stream_url.replace("{{USERNAME}}", username)
        if password:
            stream_url = stream_url.replace("{{PASSWORD}}", password)
        return stream_url

    def _build_stream_url(
        self,
        original_url: str,
        persisted_stream_url: str | None,
        username: str,
        password: str,
    ) -> str | None:
        if persisted_stream_url:
            return self._interpolate_stream_url_template(persisted_stream_url, username, password)
        if not original_url or not username or not password:
            return None
        stream_id, extension, content_type_detected = self._extract_stream_id(original_url)
        if not stream_id:
            return None
        base_url = self._https_base_url
        if content_type_detected == "live":
            return f"{base_url}/{username}/{password}/{stream_id}"
        if extension:
            return (
                f"{base_url}/{content_type_detected}/{username}/{password}/{stream_id}.{extension}"
            )
        return f"{base_url}/{content_type_detected}/{username}/{password}/{stream_id}"

    @staticmethod
    def _is_placeholder_logo(url: str | None) -> bool:
        if not url:
            return True
        lower_url = url.lower()
        return "placeholder" in lower_url or "via.placeholder.com" in lower_url

    @classmethod
    def _build_tmdb_image_url(cls, path: str | None, size: str = "w500") -> str:
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{cls.TMDB_IMAGE_BASE_URL}/{size}{path}"

    def _select_catalog_image_url(self, item: dict[str, Any], content_type: str) -> str:
        if content_type in ("movies", "series"):
            tmdb_url = self._build_tmdb_image_url(item.get("poster_path"))
            if tmdb_url:
                return tmdb_url
        logo = item.get("logo") or ""
        if logo:
            return logo
        default_map = {
            "movies": self.DEFAULT_IMAGE_MOVIE,
            "series": self.DEFAULT_IMAGE_SERIES,
            "channels": self.DEFAULT_IMAGE_CHANNEL,
        }
        return default_map.get(content_type, self.DEFAULT_IMAGE_CHANNEL)

    def _parse_content_item(
        self,
        row: dict[str, Any],
        content_type: str,
        username: str = "",
        password: str = "",
    ) -> dict[str, Any]:
        original_url = row.get("url") or ""
        persisted_stream_url = row.get("stream_url") or None
        if persisted_stream_url == "":
            persisted_stream_url = None
        provider_id = row.get("provider_id") or None
        if provider_id:
            stream_id = str(provider_id)
            url_content_type = "live" if content_type == "channels" else content_type.rstrip("s")
        else:
            extracted_id, _ext, url_content_type = self._extract_stream_id(original_url)
            stream_id = extracted_id or ""
        internal_id = str(row.get("id") or stream_id)
        if persisted_stream_url and username and password:
            stream_url = self._interpolate_stream_url_template(
                persisted_stream_url, username, password
            )
        elif stream_id and username and password:
            base_url = self._https_base_url
            if url_content_type == "live":
                stream_url = f"{base_url}/{username}/{password}/{stream_id}"
            else:
                if original_url and "." in original_url.split("/")[-1]:
                    ext = original_url.split("/")[-1].rsplit(".", 1)[-1]
                else:
                    ext = "ts"
                stream_url = (
                    f"{base_url}/{url_content_type}/{username}/{password}/{stream_id}.{ext}"
                )
        else:
            stream_url = None
        base_item = {
            "id": internal_id,
            "num": row.get("numero"),
            "nombre": row.get("nombre") or "",
            "nombre_normalizado": row.get("nombre_normalizado") or row.get("nombre") or "",
            "logo": row.get("logo") or "",
            "grupo": row.get("grupo") or "",
            "grupo_normalizado": row.get("grupo_normalizado") or row.get("grupo") or "",
            "country": row.get("country"),
            "countries": row.get("countries"),
            "provider_id": provider_id,
            "url": original_url,
            "stream_url": stream_url,
        }
        if content_type == "channels":
            base_item["tvg_id"] = row.get("tvg_id")
        elif content_type == "series":
            base_item["serie_name"] = row.get("serie_name") or ""
            base_item["temporada"] = row.get("temporada")
            base_item["episodio"] = row.get("episodio")
        if content_type in ("movies", "series"):
            if row.get("overview_es"):
                base_item["overview"] = row["overview_es"]
            elif row.get("overview_en"):
                base_item["overview"] = row["overview_en"]
            base_item["overview_es"] = row.get("overview_es")
            base_item["overview_en"] = row.get("overview_en")
            base_item["rating"] = row.get("vote_average")
            base_item["vote_count"] = row.get("vote_count")
            base_item["genres"] = row.get("genres")
            base_item["poster_path"] = row.get("tmdb_poster_path") or row.get("poster_path")
            base_item["backdrop_path"] = row.get("backdrop_path")
            base_item["runtime_minutes"] = row.get("runtime_minutes")
            base_item["tagline"] = row.get("tagline")
            base_item["release_date"] = row.get("release_date")
            base_item["year"] = row.get("year")
            base_item["tmdb_id"] = row.get("tmdb_id")
            base_item["tmdb_title"] = row.get("tmdb_title")
            base_item["popularity"] = row.get("popularity")
            base_item["status"] = row.get("status")
            if content_type == "series":
                base_item["total_seasons"] = row.get("total_seasons")
        return base_item

    def _to_android_catalog_item(
        self,
        row: dict[str, Any],
        content_type: str,
        username: str = "",
        password: str = "",
    ) -> dict[str, Any]:
        parsed = self._parse_content_item(row, content_type, username, password)
        original_title = parsed.get("nombre") or ""
        original_group = parsed.get("grupo") or ""
        title = parsed.get("nombre_normalizado") or original_title
        group = parsed.get("grupo_normalizado") or original_group
        result = {
            "id": parsed.get("provider_id") or parsed.get("id") or "",
            "provider_id": parsed.get("provider_id") or "",
            "type": map_android_type(content_type),
            "title": title,
            "normalized_title": title,
            "original_title": original_title,
            "subtitle": group,
            "description": group,
            "image_url": self._select_catalog_image_url(parsed, content_type),
            "group": group,
            "normalized_group": group,
            "original_group": original_group,
            "badge_text": (
                group[:8]
                if content_type == "channels"
                else ("CINE" if content_type == "movies" else "SERIE")
            ),
            "channel_number": parsed.get("num") if content_type == "channels" else None,
            "language_label": self._countries_label(parsed.get("countries")),
            "countries": parsed.get("countries"),
            "countries_detail": self._countries_detail(parsed.get("countries")),
            "series_name": (
                (parsed.get("serie_name") or parsed.get("nombre_normalizado") or original_title)
                if content_type == "series"
                else None
            ),
            "season_number": (parsed.get("temporada") if content_type == "series" else None),
            "episode_number": (parsed.get("episodio") if content_type == "series" else None),
            "stream_url": parsed.get("stream_url") or "",
        }
        if content_type in ("movies", "series"):
            result.update(
                {
                    "overview": parsed.get("overview"),
                    "overview_es": parsed.get("overview_es"),
                    "overview_en": parsed.get("overview_en"),
                    "rating": parsed.get("rating"),
                    "vote_count": parsed.get("vote_count"),
                    "genres": parsed.get("genres"),
                    "poster_path": parsed.get("poster_path"),
                    "backdrop_path": parsed.get("backdrop_path"),
                    "tagline": parsed.get("tagline"),
                    "release_date": parsed.get("release_date"),
                    "year": parsed.get("year"),
                    "tmdb_id": parsed.get("tmdb_id"),
                    "tmdb_title": parsed.get("tmdb_title"),
                    "popularity": parsed.get("popularity"),
                    "status": parsed.get("status"),
                }
            )
        return result

    def _to_android_series_from_catalog(
        self, row: dict[str, Any], username: str = "", password: str = ""
    ) -> dict[str, Any]:
        serie_name = row.get("title", "")
        normalized_title = serie_name.lower().strip() if serie_name else ""
        original_group = row.get("group_normalizado") or ""
        group = original_group
        overview = row.get("overview_es") or row.get("overview_en") or ""
        poster = row.get("poster_path") or ""
        backdrop = row.get("backdrop_path") or ""
        return {
            "id": row.get("provider_id") or str(row.get("id") or ""),
            "provider_id": row.get("provider_id") or "",
            "type": "series",
            "title": serie_name,
            "normalized_title": normalized_title,
            "original_title": serie_name,
            "subtitle": group,
            "description": overview or group,
            "image_url": self._build_tmdb_image_url(poster)
            or row.get("logo")
            or self.DEFAULT_IMAGE_SERIES,
            "group": group,
            "normalized_group": group,
            "badge_text": "SERIE",
            "series_name": serie_name,
            "series_key": row.get("series_key") or "",
            "season_number": None,
            "episode_number": None,
            "stream_url": "",
            "total_episodes": row.get("total_episodes", 0),
            "total_seasons": row.get("total_seasons", 0),
            "year": row.get("year"),
            "language_label": self._countries_label(row.get("countries")),
            "countries": row.get("countries"),
            "countries_detail": self._countries_detail(row.get("countries")),
            "overview": overview,
            "overview_es": row.get("overview_es") or "",
            "overview_en": row.get("overview_en") or "",
            "rating": row.get("vote_average"),
            "vote_count": row.get("vote_count"),
            "genres": row.get("genres"),
            "poster_path": poster,
            "backdrop_path": backdrop,
            "tagline": row.get("tagline"),
            "release_date": str(row.get("release_date") or ""),
            "tmdb_id": row.get("tmdb_id"),
            "tmdb_title": row.get("tmdb_title") or serie_name,
            "popularity": row.get("popularity"),
            "status": row.get("status"),
        }

    def _to_android_movie_from_catalog(
        self, row: dict[str, Any], username: str = "", password: str = ""
    ) -> dict[str, Any]:
        stream_options = row.get("stream_options") or []
        first_stream = stream_options[0] if stream_options else {}
        overview = row.get("overview_es") or row.get("overview_en") or ""
        overview_es = row.get("overview_es") or ""
        poster = row.get("poster_path") or ""
        backdrop = row.get("backdrop_path") or ""
        tmdb_title = row.get("tmdb_title") or row.get("title") or ""
        return {
            "id": row.get("provider_id") or str(row.get("id", "")),
            "provider_id": row.get("provider_id") or "",
            "type": "movie",
            "title": row.get("title", ""),
            "normalized_title": row.get("title", ""),
            "subtitle": "",
            "description": overview,
            "image_url": self._build_tmdb_image_url(poster)
            or row.get("logo")
            or self.DEFAULT_IMAGE_MOVIE,
            "group": "",
            "normalized_group": "",
            "badge_text": "CINE",
            "series_name": None,
            "season_number": None,
            "episode_number": None,
            "stream_url": ContentServiceV2._resolve_stream_url(stream_options, username, password),
            "stream_options": ContentServiceV2._resolve_stream_options(
                stream_options, username, password
            ),
            "stream_label": first_stream.get("label", "Ver"),
            "language_label": self._countries_label(row.get("countries")),
            "countries": row.get("countries"),
            "countries_detail": self._countries_detail(row.get("countries")),
            "overview": overview,
            "overview_es": overview_es,
            "overview_en": row.get("overview_en") or "",
            "rating": row.get("vote_average"),
            "vote_count": row.get("vote_count"),
            "genres": row.get("genres"),
            "poster_path": poster,
            "backdrop_path": backdrop,
            "tagline": row.get("tagline"),
            "release_date": str(row.get("release_date") or ""),
            "year": row.get("year"),
            "runtime_minutes": row.get("runtime_minutes"),
            "tmdb_id": row.get("tmdb_id"),
            "tmdb_title": tmdb_title,
            "popularity": row.get("popularity"),
            "status": row.get("status"),
        }

    def _to_android_series_group_item(
        self, row: dict[str, Any], username: str = "", password: str = ""
    ) -> dict[str, Any]:
        catalog_title = row.get("title", "")
        tmdb_title = row.get("tmdb_title") or ""
        serie_name = tmdb_title or catalog_title
        normalized_title = serie_name.lower().strip() if serie_name else ""
        group = row.get("group_normalizado") or ""
        provider_id = row.get("provider_id") or ""
        release_date = row.get("release_date")
        year = None
        if release_date:
            try:
                year = int(str(release_date)[:4])
            except (ValueError, TypeError):
                pass
        if year is None:
            year = row.get("year")
        return {
            "id": provider_id or row.get("id") or "",
            "provider_id": provider_id,
            "type": "series_group",
            "title": serie_name,
            "normalized_title": normalized_title,
            "original_title": serie_name,
            "subtitle": group,
            "description": group,
            "image_url": self._build_tmdb_image_url(row.get("poster_path"))
            or row.get("logo")
            or self.DEFAULT_IMAGE_SERIES,
            "group": group,
            "normalized_group": group,
            "original_group": group,
            "badge_text": "SERIE",
            "series_name": serie_name,
            "season_number": None,
            "episode_number": None,
            "total_episodes": row.get("total_episodes", 0),
            "year": year,
            "language_label": self._countries_label(row.get("countries")),
            "countries": row.get("countries"),
            "countries_detail": self._countries_detail(row.get("countries")),
            "stream_url": "",
            "overview": row.get("overview_es") or row.get("overview_en"),
            "overview_es": row.get("overview_es"),
            "overview_en": row.get("overview_en"),
            "rating": row.get("vote_average"),
            "vote_count": row.get("vote_count"),
            "genres": row.get("genres"),
            "poster_path": row.get("poster_path"),
            "backdrop_path": row.get("backdrop_path"),
            "tagline": row.get("tagline"),
            "release_date": row.get("release_date"),
            "tmdb_id": row.get("tmdb_id"),
            "tmdb_title": tmdb_title,
            "popularity": row.get("popularity"),
            "status": row.get("status"),
            "total_seasons": row.get("total_seasons"),
        }

    def _get_movies_catalog_page_raw(
        self,
        page: int,
        page_size: int,
        group: str | None = None,
        upper_group: str | None = None,
        country: str | None = None,
        search: str | None = None,
        year: int | None = None,
        genre: str | None = None,
    ) -> tuple[list[dict], int]:
        rows, total = self.content_repo.get_movies_catalog_page(
            page=page,
            page_size=page_size,
            group=group,
            upper_group=upper_group,
            country=country,
            search=search,
            year=year,
            genre=genre,
        )
        # Ensure countries field is populated from stream data if missing
        for row in rows:
            row["countries"] = self._resolve_countries(row)
        return rows, total

    @staticmethod
    def _resolve_countries(row: dict[str, Any]) -> list[str]:
        co = row.get("countries")
        if co and isinstance(co, list) and len(co) > 0:
            return sorted(c for c in set(co) if c and c != "UNKNOWN")
        legacy = row.get("country")
        stream_opts = row.get("stream_options") or []
        result = set()
        if legacy and legacy != "UNKNOWN":
            result.add(legacy)
        for s in stream_opts:
            c = s.get("country")
            if c and c != "UNKNOWN":
                result.add(c)
        return sorted(result)

    def _get_distinct_series_groups_catalog_raw(
        self,
        page: int,
        page_size: int,
        group: str | None = None,
        upper_group: str | None = None,
        country: str | None = None,
        search: str | None = None,
        year: int | None = None,
        genre: str | None = None,
    ) -> dict:
        result = self.series_repo.get_distinct_series_groups_catalog(
            page=page,
            page_size=page_size,
            group=group,
            upper_group=upper_group,
            country=country,
            search=search,
            year=year,
            genre=genre,
        )
        items = result.get("items") or []
        for row in items:
            row["countries"] = self._resolve_countries(row)
        result["items"] = items
        return result

    def _fetch_section_page(
        self,
        content_type: str,
        gp: dict,
        page: int,
        page_size: int,
        username: str,
        password: str,
        country: str | None,
    ) -> tuple[list[dict], int]:
        use_upper_group = "group" in gp
        group_filter = gp.get("pattern") if not use_upper_group else None
        upper_group = gp.get("group") if use_upper_group else None
        effective_country = country or gp.get("country")
        year = gp.get("year")
        if content_type == "series":
            result = self._get_distinct_series_groups_catalog_raw(
                page=page,
                page_size=page_size,
                group=group_filter,
                upper_group=upper_group,
                country=effective_country,
                search=None,
                year=year,
            )
            raw_items = result.get("items") or []
            total = result.get("total", 0)
            items = [
                self._to_android_series_group_item(row, username, password) for row in raw_items
            ]
            return items, total
        elif content_type == "movies":
            items_cat, total_cat = self._get_movies_catalog_page_raw(
                page=page,
                page_size=page_size,
                group=group_filter,
                upper_group=upper_group,
                country=effective_country,
                search=None,
                year=year,
            )
            if items_cat:
                return [
                    self._to_android_movie_from_catalog(row, username, password)
                    for row in items_cat
                ], total_cat
            return [], 0
        return [], 0

    def _build_home_sections(
        self,
        content_type: str,
        page: int,
        page_size: int,
        username: str,
        password: str,
        country: str | None,
    ) -> list[dict]:
        sections = []
        for gp in self.SECTION_PATTERNS.get(content_type, []):
            items, total = self._fetch_section_page(
                content_type=content_type,
                gp=gp,
                page=page,
                page_size=page_size,
                username=username,
                password=password,
                country=country,
            )
            if items:
                pages = (total + page_size - 1) // page_size if total > 0 else 0
                sections.append(
                    {
                        "title": gp["title"],
                        "items": items,
                        "page": page,
                        "page_size": page_size,
                        "has_more": page < pages,
                        "has_next": page < pages,
                        "group_name": gp.get("group") or gp.get("pattern"),
                        "section_title": gp["title"],
                        "year": gp.get("year"),
                        "total": total,
                        "pages": pages,
                    }
                )
        return sections

    def _resolve_dailymotion_stream(self, provider_access_id: str) -> dict | None:
        metadata_url = f"https://www.dailymotion.com/player/metadata/video/{provider_access_id}"
        try:
            response = requests.get(
                metadata_url,
                params={"embedder": self.REPLAY_METADATA_EMBEDDER},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None
        source = self._pick_best_dailymotion_quality(payload.get("qualities") or {})
        if not source:
            return None
        return {
            "stream_url": source.get("url"),
            "stream_format": source.get("type") or "application/x-mpegURL",
            "provider": "dailymotion",
            "provider_video_id": payload.get("id"),
        }

    @staticmethod
    def _pick_best_dailymotion_quality(
        quality_sources: dict[str, list[dict]],
    ) -> dict | None:
        numeric_sources = []
        for label, sources in quality_sources.items():
            if str(label).isdigit() and sources:
                numeric_sources.append((int(str(label)), sources[0]))
        if numeric_sources:
            numeric_sources.sort(key=lambda item: item[0], reverse=True)
            return numeric_sources[0][1]
        auto_sources = quality_sources.get("auto") or []
        if auto_sources:
            return auto_sources[0]
        for sources in quality_sources.values():
            if sources:
                return sources[0]
        return None

    def _countries_label(self, countries: Any) -> str:
        if not countries or not isinstance(countries, list):
            return ""
        valid = [c for c in countries if c and isinstance(c, str) and c != "UNKNOWN"]
        return ", ".join(
            sorted(self.COUNTRY_NAMES.get(c, c) for c in valid)
        )

    def _countries_detail(self, countries: Any) -> list[dict[str, str]]:
        if not countries or not isinstance(countries, list):
            return []
        valid = [c for c in countries if c and isinstance(c, str) and c != "UNKNOWN"]
        return sorted(
            [{"value": c, "label": self.COUNTRY_NAMES.get(c, c)} for c in valid],
            key=lambda x: x["label"],
        )

    @staticmethod
    def _guess_stream_format(url: str) -> str:
        lowered = url.lower()
        if ".m3u8" in lowered:
            return "application/x-mpegURL"
        if ".mp4" in lowered:
            return "video/mp4"
        return "application/octet-stream"

    @staticmethod
    def _provider_from_url(url: str) -> str:
        parsed = urlparse(url)
        if "dailymotion" in parsed.netloc or "dmcdn" in parsed.netloc:
            return "dailymotion"
        return parsed.netloc or "unknown"

    @staticmethod
    def _extract_dailymotion_access_id(url: str) -> str | None:
        if not url:
            return None
        patterns = [
            r"/embed/video/([A-Za-z0-9]+)",
            r"/manifest/video/([A-Za-z0-9]+)\.m3u8",
            r"/video/([A-Za-z0-9]+)\.m3u8",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def get_movie(self, movie_id: str) -> dict | None:
        return self.content_repo.get_movie_with_metadata(movie_id)

    def get_series(self, series_id: str) -> dict | None:
        return self.series_repo.get_with_metadata(series_id)

    def get_content_item(
        self, content_type: str, item_id: str, username: str = "", password: str = ""
    ) -> dict | None:
        if content_type in ("movie", "movies"):
            row = self.get_movie(item_id)
            if row is not None:
                self._ensure_stream_url(row, username, password)
            return row
        elif content_type in ("series", "serie"):
            row = self.get_series(item_id)
            if row is None:
                row = self.series_repo.get_catalog_by_episode_provider_id(item_id)
            if row is not None:
                self._ensure_stream_url(row, username, password)
            return row
        elif content_type == "channels":
            channel = self.channel_repo.get_by_id(item_id)
            if not channel:
                channel = self.channel_repo.get_by_provider_id(item_id)
            if channel:
                return {
                    "id": str(channel.id),
                    "provider_id": channel.provider_id,
                    "nombre": channel.nombre,
                    "logo": channel.logo,
                    "url": channel.url,
                }
        return None

    def _ensure_stream_url(self, row: dict, username: str, password: str) -> None:
        if row.get("stream_url"):
            return
        streams = row.get("stream_options") or []
        if not streams:
            return
        raw_url = streams[0].get("url", "")
        if not raw_url:
            return
        if username and password:
            row["stream_url"] = raw_url.replace("{{USERNAME}}", username).replace("{{PASSWORD}}", password)
        else:
            row["stream_url"] = raw_url

    def get_movies_paginated(
        self,
        page: int,
        page_size: int,
        country: str | None = None,
        search: str | None = None,
        year: int | None = None,
        genre: str | None = None,
        group: str | None = None,
    ) -> tuple[list[dict], int]:
        return self.content_repo.get_movies_paginated(page, page_size, country, search, year, genre, group)

    @staticmethod
    def _resolve_stream_url(stream_options: list[dict], username: str, password: str) -> str:
        if not stream_options:
            return ""
        url = stream_options[0].get("url", "")
        if username and password:
            url = url.replace("{{USERNAME}}", username).replace("{{PASSWORD}}", password)
        return url

    @staticmethod
    def _resolve_stream_options(stream_options: list, username: str, password: str) -> list:
        if not stream_options:
            return []
        resolved = []
        for opt in stream_options:
            url = opt.get("url", "")
            if username and password:
                url = url.replace("{{USERNAME}}", username).replace("{{PASSWORD}}", password)
            resolved.append(
                {
                    "url": url,
                    "label": opt.get("label", "Ver"),
                    "country": opt.get("country", ""),
                    "quality": opt.get("quality"),
                    "provider_id": opt.get("provider_id", ""),
                }
            )
        return resolved

    @staticmethod
    def _to_android_episode(
        row: dict,
        series_title: str,
        series_catalog_id: str,
        username: str = "",
        password: str = "",
        base_url: str = "",
    ) -> dict:
        stream_options = row.get("stream_options") or []
        first_stream = stream_options[0] if stream_options else {}
        provider_id = first_stream.get("provider_id", "")
        episode_id = row.get("episode_id", "")

        stream_url = ContentServiceV2._resolve_stream_url(stream_options, username, password)
        if stream_url and not stream_url.startswith("http") and base_url and provider_id:
            raw_url = stream_options[0].get("url", "")
            ext = "ts"
            if raw_url and "." in raw_url.split("/")[-1]:
                ext = raw_url.split("/")[-1].rsplit(".", 1)[-1]
            stream_url = f"{base_url}/series/{username}/{password}/{provider_id}.{ext}"

        resolved_options = ContentServiceV2._resolve_stream_options(
            stream_options, username, password
        )
        for i, opt in enumerate(resolved_options):
            url = opt.get("url", "")
            opt_pid = opt.get("provider_id", "")
            raw_url = stream_options[i].get("url", "") if i < len(stream_options) else ""
            if url and not url.startswith("http") and base_url and opt_pid:
                opt_ext = "ts"
                if raw_url and "." in raw_url.split("/")[-1]:
                    opt_ext = raw_url.split("/")[-1].rsplit(".", 1)[-1]
                opt["url"] = f"{base_url}/series/{username}/{password}/{opt_pid}.{opt_ext}"

        return {
            "id": episode_id or provider_id,
            "provider_id": episode_id or provider_id,
            "type": "series",
            "title": row.get("title") or series_title,
            "normalized_title": row.get("title") or series_title,
            "subtitle": f"S{row.get('season_number', 0)} · E{row.get('episode_number', 0)}",
            "description": "",
            "image_url": "",
            "group": "",
            "normalized_group": "",
            "badge_text": "SERIE",
            "series_name": series_title,
            "series_key": series_catalog_id,
            "season_number": row.get("season_number"),
            "episode_number": row.get("episode_number"),
            "stream_url": stream_url,
            "stream_options": resolved_options,
            "stream_label": first_stream.get("label", "Ver"),
            "language_label": first_stream.get("country", ""),
            "total_seasons": None,
            "overview": row.get("overview", ""),
            "overview_en": row.get("overview_en", ""),
            "title_en": row.get("title_en", ""),
            "air_date": str(row.get("air_date", "")) if row.get("air_date") else None,
            "still_path": row.get("still_path", ""),
            "runtime": row.get("runtime"),
            "vote_average": row.get("vote_average"),
            "vote_count": row.get("vote_count"),
            "episode_type": row.get("episode_type", ""),
        }

    def get_replays(
        self,
        page: int = 1,
        page_size: int = 24,
        event_type: str | None = None,
        search: str | None = None,
    ) -> dict:
        from sqlalchemy import and_, desc, func, or_, select

        from app.models.replay import Replay

        filters = []
        if event_type:
            filters.append(Replay.event_type == event_type)
        if search:
            filters.append(
                or_(
                    Replay.title.ilike(f"%{search}%"),
                    Replay.event_name.ilike(f"%{search}%"),
                )
            )
        count_stmt = select(func.count()).select_from(Replay)
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total = self.session.execute(count_stmt).scalar() or 0

        data_stmt = select(Replay)
        if filters:
            data_stmt = data_stmt.where(and_(*filters))
        data_stmt = (
            data_stmt.order_by(desc(Replay.event_date), desc(Replay.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = list(self.session.execute(data_stmt).scalars().all())
        items = [self._serialize_replay(r) for r in rows]
        return self._build_paginated_payload(items, total, page, page_size)

    def get_replay(self, slug: str) -> dict | None:
        replay = self.replay_repo.get_by_slug(slug)
        if not replay:
            return None
        return self._serialize_replay(replay)

    @staticmethod
    def _serialize_replay(r) -> dict:
        from datetime import date

        video_sources = r.video_sources or []
        event_date = r.event_date
        if isinstance(event_date, date):
            event_date = event_date.isoformat()
        return {
            "slug": r.slug,
            "source_site": r.source_site or "",
            "title": r.title,
            "event_name": r.event_name,
            "event_type": r.event_type,
            "event_date": event_date,
            "post_url": r.post_url or "",
            "featured_image_url": r.featured_image_url,
            "description": r.description,
            "video_sources": video_sources,
            "match_card": r.match_card or [],
        }

    @staticmethod
    def _build_paginated_payload(
        items: list,
        total: int,
        page: int,
        page_size: int,
        extra: dict | None = None,
    ) -> dict:
        pages = (total + page_size - 1) // page_size if total else 0
        payload = {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1,
        }
        if extra:
            payload.update(extra)
        return payload

    def _find_series_catalog(self, identifier: str) -> dict | None:
        catalog = self.series_repo.get_by_title(identifier)
        if catalog:
            return catalog
        if identifier.isdigit():
            catalog = self.series_repo.get_catalog_by_provider_id(identifier)
            if catalog:
                return catalog
        return None

    def get_episodes_by_serie_name(
        self,
        serie_name: str,
        username: str = "",
        password: str = "",
    ) -> list[dict]:
        catalog = self._find_series_catalog(serie_name)
        if not catalog:
            return []
        catalog_id = str(catalog["id"])
        series_title = catalog.get("title") or catalog.get("tmdb_title") or serie_name
        items, _total, _seasons = self.series_repo.get_episodes_with_streams(
            catalog_id, page=1, page_size=1000
        )
        return [
            self._to_android_episode(row, series_title, catalog_id, username, password, base_url=self._https_base_url)
            for row in (items or [])
        ]

    def get_episodes_by_serie_name_paginated(
        self,
        serie_name: str,
        username: str = "",
        password: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> dict:
        catalog = self._find_series_catalog(serie_name)
        if not catalog:
            return self._build_paginated_payload([], 0, page, page_size)
        catalog_id = str(catalog["id"])
        series_title = catalog.get("title") or catalog.get("tmdb_title") or serie_name
        items, total, seasons = self.series_repo.get_episodes_with_streams(
            catalog_id, page=page, page_size=page_size
        )
        return self._build_paginated_payload(
            [
                self._to_android_episode(row, series_title, catalog_id, username, password, base_url=self._https_base_url)
                for row in (items or [])
            ],
            total or 0,
            page,
            page_size,
            extra={
                "serie_name": series_title,
                "episodes": items or [],
                "total_episodes": total or 0,
                "seasons": seasons or 0,
            },
        )

    def get_all_content_bulk(self, content_type: str) -> dict:
        json_data = self._load_static_json(content_type)
        if json_data:
            return json_data
        if content_type == "channels":
            return self._get_all_channels_from_db()
        elif content_type == "movies":
            return self._get_all_movies_from_db()
        elif content_type == "series":
            return self._get_all_series_from_db()
        return {"items": [], "total": 0}

    def get_all_channels_bulk(self) -> dict:
        json_data = self._load_static_json("channels")
        if json_data:
            return {
                "items": json_data.get("items", []),
                "total": json_data.get("total", 0),
            }
        return self._get_all_channels_from_db()

    @staticmethod
    def _get_static_json_path(content_type: str) -> str | None:
        base_dirs = [
            "/app/data/json",
            os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "..",
                "walactv-scrapper",
                "data",
                "json",
            ),
            "data/json",
        ]
        filename_map = {
            "channels": "channels.json",
            "movies": "movies.json",
            "series": "series.json",
        }
        filename = filename_map.get(content_type)
        if not filename:
            return None
        for base_dir in base_dirs:
            path = os.path.join(base_dir, filename)
            if os.path.exists(path):
                return path
        return None

    def _load_static_json(self, content_type: str) -> dict | None:
        json_path = self._get_static_json_path(content_type)
        if not json_path or not os.path.exists(json_path):
            return None
        try:
            gz_path = f"{json_path}.gz"
            if os.path.exists(gz_path):
                with gzip.open(gz_path, "rt", encoding="utf-8") as f:
                    return json.load(f)
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error cargando JSON estático {content_type}: {e}")
            return None

    def _get_all_channels_from_db(self) -> dict:
        from app.models.channel import Channel

        stmt = select(Channel).order_by(Channel.nombre)
        rows = list(self.session.execute(stmt).scalars().all())
        items = []
        for r in rows:
            items.append(
                {
                    "id": str(r.id) if r.id else "",
                    "logo": r.logo or "",
                    "provider_id": str(r.provider_id) if r.provider_id else "",
                    "country": r.country or "",
                    "nombre_normalizado": r.nombre_normalizado or r.nombre or "",
                    "grupo_normalizado": r.grupo_normalizado or r.grupo or "",
                    "nombre": r.nombre or "",
                    "grupo": r.grupo or "",
                    "url": r.url or "",
                    "numero": r.numero,
                    "tvg_id": r.tvg_id or "",
                }
            )
        return {"items": items, "total": len(items)}

    def _get_all_movies_from_db(self) -> dict:
        from app.models.content import MovieCatalog

        stmt = select(MovieCatalog).order_by(MovieCatalog.title)
        rows = list(self.session.execute(stmt).scalars().all())
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.provider_id or str(r.id) if r.id else "",
                    "provider_id": r.provider_id or "",
                    "title": r.title or "",
                    "nombre_dedup_key": r.nombre_dedup_key or "",
                    "country": r.country or "",
                    "countries": r.countries,
                    "year": r.year,
                    "poster_path": getattr(r, 'poster_path', None) or "",
                    "backdrop_path": getattr(r, 'backdrop_path', None) or "",
                }
            )
        return {"items": items, "total": len(items)}

    def _get_all_series_from_db(self) -> dict:
        from app.models.series import SeriesCatalog

        stmt = select(SeriesCatalog).order_by(SeriesCatalog.title)
        rows = list(self.session.execute(stmt).scalars().all())
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.provider_id or str(r.id) if r.id else "",
                    "provider_id": r.provider_id or "",
                    "title": r.title or "",
                    "nombre_normalizado": getattr(r, 'nombre_normalizado', None) or "",
                    "country": r.country or "",
                    "countries": r.countries,
                    "year": r.year,
                    "poster_path": getattr(r, 'poster_path', None) or "",
                    "backdrop_path": getattr(r, 'backdrop_path', None) or "",
                    "group_normalizado": r.group_normalizado or "",
                }
            )
        return {"items": items, "total": len(items)}

    def get_android_content_list(
        self,
        content_type: str,
        page: int,
        page_size: int,
        group: str | None = None,
        country: str | None = None,
        search: str | None = None,
        year: int | None = None,
        username: str = "",
        password: str = "",
        genre: str | None = None,
    ) -> dict:
        if content_type == "series":
            result = self._get_distinct_series_groups_catalog_raw(
                page=page,
                page_size=page_size,
                group=group,
                country=country,
                search=search,
                year=year,
                genre=genre,
            )
            items = result.get("items") or []
            total = result.get("total", 0)
            parsed_items = [
                self._to_android_series_from_catalog(row, username, password) for row in items
            ]
            return self._build_paginated_payload(parsed_items, total, page, page_size)
        if content_type in ("movies", "channels"):
            if content_type == "movies":
                rows, total = self.get_movies_paginated(page, page_size, country, search, year, genre, group)
            else:
                channels, total = self.get_channels_paginated(
                    page, page_size, country, group, search
                )
                rows = [dict(c) for c in channels]
            android_items = [
                self._to_android_catalog_item(row, content_type, username, password) for row in rows
            ]
            return self._build_paginated_payload(android_items, total, page, page_size)
        return self._build_paginated_payload([], 0, page, page_size)

    def get_section_page(
        self,
        content_type: str,
        section_title: str,
        page: int,
        page_size: int = 24,
        username: str = "",
        password: str = "",
        country: str | None = None,
    ) -> dict | None:
        patterns = self.SECTION_PATTERNS.get(content_type, [])
        gp = next((p for p in patterns if p["title"].upper() == section_title.upper()), None)
        if not gp:
            return None
        items, total = self._fetch_section_page(
            content_type=content_type,
            gp=gp,
            page=page,
            page_size=page_size,
            username=username,
            password=password,
            country=country,
        )
        pages = (total + page_size - 1) // page_size if total > 0 else 0
        return {
            "title": gp["title"],
            "items": items,
            "page": page,
            "page_size": page_size,
            "has_more": page < pages,
            "has_next": page < pages,
            "has_prev": page > 1,
            "total": total,
            "pages": pages,
        }

    def get_home_catalog_new(
        self,
        username: str = "",
        country: str | None = None,
        password: str = "",
        page_size: int = 24,
    ) -> dict:
        counts = self.get_content_stats("channels")
        counts.update(self.get_content_stats("movies"))
        counts.update(self.get_content_stats("series"))
        return {
            "movie_sections": self._build_home_sections(
                "movies",
                page=1,
                page_size=page_size,
                username=username,
                password=password,
                country=country,
            ),
            "series_sections": self._build_home_sections(
                "series",
                page=1,
                page_size=page_size,
                username=username,
                password=password,
                country=country,
            ),
            "stats": {
                "channels": counts.get("channels", {}).get("total", 0),
                "movies": counts.get("movies", {}).get("total", 0),
                "series": counts.get("series", {}).get("total", 0),
            },
        }

    def search_catalog(
        self,
        query: str,
        types: list,
        page: int = 1,
        page_size: int = 50,
        username: str = "",
        password: str = "",
        country: str | None = None,
        genre: str | None = None,
    ) -> dict:
        requested_types = [ct for ct in types if ct in ("channels", "movies", "series", "events")]
        if not requested_types:
            requested_types = ["channels", "movies", "series"]
        merged_items: list[dict] = []
        for content_type in requested_types:
            if content_type == "movies":
                rows, _ = self._get_movies_catalog_page_raw(1, 1000, search=query, country=country, genre=genre)
                merged_items.extend(
                    self._to_android_movie_from_catalog(row, username, password) for row in rows
                )
            elif content_type == "series":
                result = self._get_distinct_series_groups_catalog_raw(
                    page=1, page_size=1000, search=query, country=country, genre=genre
                )
                merged_items.extend(
                    self._to_android_series_from_catalog(row, username, password)
                    for row in (result.get("items") or [])
                )
            elif content_type == "channels":
                channels, _ = self.channel_repo.get_paginated(1, 1000, search=query, country=country)
                rows = [
                    {
                        "id": str(c.id),
                        "provider_id": c.provider_id,
                        "nombre": c.nombre,
                        "nombre_normalizado": c.nombre_normalizado,
                        "logo": c.logo,
                        "grupo": c.grupo,
                        "grupo_normalizado": c.grupo_normalizado,
                        "country": c.country,
                        "url": c.url,
                        "numero": c.numero,
                        "tvg_id": c.tvg_id,
                    }
                    for c in channels
                ]
                merged_items.extend(
                    self._to_android_catalog_item(row, content_type, username, password)
                    for row in rows
                )
            elif content_type == "events":
                merged_items.extend(
                    self._search_events(query, username, password)
                )
        merged_items.sort(key=lambda item: item.get("title") or "")
        total = len(merged_items)
        offset = (page - 1) * page_size
        paged_items = merged_items[offset : offset + page_size]
        pages = (total + page_size - 1) // page_size if total else 0
        return {
            "items": paged_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "has_next": page < pages,
            "has_prev": page > 1,
            "types": requested_types,
        }

    def _search_events(
        self, query: str, username: str = "", password: str = ""
    ) -> list[dict[str, Any]]:
        from datetime import date, timedelta

        from sqlalchemy import text

        query_lower = query.lower().strip()
        all_events: list[dict[str, Any]] = []

        for delta in range(-7, 8):
            target_date = date.today() + timedelta(days=delta)
            try:
                sql = text("SELECT * FROM get_eventos_fecha_con_channels(:fecha)")
                rows = self.session.execute(sql, {"fecha": target_date}).mappings().all()
                for row in rows:
                    evento = dict(row)
                    competicion = (evento.get("competicion") or "").lower()
                    equipos = (evento.get("equipos") or "").lower()
                    categoria = (evento.get("categoria") or "").lower()
                    subtitulo = (evento.get("subtitulo_competicion") or "").lower()
                    if (
                        query_lower in competicion
                        or query_lower in equipos
                        or query_lower in categoria
                        or query_lower in subtitulo
                    ):
                        all_events.append(evento)
            except Exception:
                continue

        seen_ids: set[str] = set()
        unique_events: list[dict[str, Any]] = []
        for evento in all_events:
            eid = str(evento.get("id", ""))
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                unique_events.append(evento)

        return [
            self._to_android_event(evento, username, password)
            for evento in unique_events
        ]

    def _to_android_event(
        self, evento: dict[str, Any], username: str = "", password: str = ""
    ) -> dict[str, Any]:
        evento_id = str(evento.get("id", ""))
        competicion = evento.get("competicion") or ""
        equipos = evento.get("equipos") or ""
        fecha = evento.get("fecha") or ""
        hora = evento.get("hora") or ""
        categoria = evento.get("categoria") or ""
        imagen = evento.get("imagen_evento") or ""
        subtitulo = evento.get("subtitulo_competicion") or ""

        title = f"{competicion} - {equipos}" if equipos else competicion
        if not title:
            title = categoria or "Evento"

        badge = f"{fecha} {hora}".strip() if fecha else ""

        canales_resueltos = evento.get("canales_resueltos") or []
        first_channel = canales_resueltos[0] if canales_resueltos else {}
        provider_id = first_channel.get("provider_id") or ""

        stream_url_raw = first_channel.get("stream_url") or ""
        if stream_url_raw and username and password:
            stream_url = self._interpolate_stream_url_template(
                stream_url_raw, username, password
            )
        elif not stream_url_raw and provider_id and username and password:
            base_url = self._https_base_url
            stream_url = f"{base_url}/{username}/{password}/{provider_id}"
        else:
            stream_url = stream_url_raw

        return {
            "id": evento_id,
            "provider_id": provider_id,
            "type": "event",
            "title": title,
            "normalized_title": title.lower(),
            "original_title": title,
            "subtitle": subtitulo or categoria,
            "description": f"{competicion}\n{equipos}\n{subtitulo}".strip(),
            "image_url": imagen,
            "group": competicion,
            "normalized_group": competicion.lower(),
            "original_group": competicion,
            "badge_text": badge,
            "channel_number": None,
            "language_label": None,
            "countries": None,
            "countries_detail": None,
            "series_name": None,
            "season_number": None,
            "episode_number": None,
            "stream_url": stream_url,
            "fecha": fecha,
            "hora": hora,
            "competicion": competicion,
            "subtitulo_competicion": subtitulo,
            "categoria": categoria,
            "equipos": equipos,
            "imagen_evento": imagen,
            "canales_resueltos": canales_resueltos,
        }

    def resolve_replay_source_stream_url(
        self,
        slug: str,
        source_index: int,
        button_index: int,
    ) -> dict | None:
        replay = self.get_replay(slug)
        if not replay:
            return None
        for group in replay.get("video_sources") or []:
            for source in group.get("sources") or []:
                if (
                    source.get("source_index") == source_index
                    and source.get("button_index") == button_index
                ):
                    provider = str(source.get("provider") or "").lower()
                    provider_access_id = source.get("provider_access_id")
                    provider_url = source.get("provider_url")
                    stream_url = source.get("stream_url")
                    stream_format = source.get("stream_format")
                    dailymotion_access_id = (
                        provider_access_id
                        or self._extract_dailymotion_access_id(
                            str(provider_url or stream_url or "")
                        )
                    )
                    if (
                        provider == "dailymotion" or dailymotion_access_id
                    ) and dailymotion_access_id:
                        refreshed = self._resolve_dailymotion_stream(str(dailymotion_access_id))
                        if refreshed:
                            return refreshed
                    direct_url = provider_url or stream_url
                    if direct_url:
                        return {
                            "stream_url": direct_url,
                            "stream_format": stream_format
                            or self._guess_stream_format(str(direct_url)),
                            "provider": provider or self._provider_from_url(str(direct_url)),
                        }
                    return None
        return None

    def to_android_catalog_item(self, row: dict, content_type: str, **kwargs) -> dict:
        return self._to_android_catalog_item(
            row,
            content_type,
            username=kwargs.get("username", ""),
            password=kwargs.get("password", ""),
        )

    def build_paginated_payload(
        self, items: list, total: int, page: int, page_size: int, **kwargs
    ) -> dict:
        extra = kwargs.get("extra")
        return self._build_paginated_payload(items, total, page, page_size, extra)

    COUNTRY_NAMES: dict[str, str] = {
        "AD": "Andorra", "AE": "Emiratos Árabes Unidos", "AF": "Afganistán",
        "AL": "Albania", "AM": "Armenia", "AR": "Argentina", "AT": "Austria",
        "AU": "Australia", "AZ": "Azerbaiyán", "BE": "Bélgica", "BG": "Bulgaria",
        "BH": "Baréin", "BR": "Brasil", "BY": "Bielorrusia", "CA": "Canadá",
        "CG": "República del Congo", "CH": "Suiza", "CY": "Chipre",
        "CZ": "República Checa", "DE": "Alemania", "DK": "Dinamarca",
        "DO": "República Dominicana", "DZ": "Argelia", "EC": "Ecuador",
        "EG": "Egipto", "ES": "España", "FI": "Finlandia", "FR": "Francia",
        "GB": "Reino Unido", "GR": "Grecia", "GT": "Guatemala", "HK": "Hong Kong",
        "EN": "Inglés",
        "HN": "Honduras", "HR": "Croacia", "HU": "Hungría", "ID": "Indonesia",
        "IE": "Irlanda", "IL": "Israel", "IN": "India", "IQ": "Irak",
        "IR": "Irán", "IS": "Islandia", "IT": "Italia", "JM": "Jamaica",
        "JO": "Jordania", "JP": "Japón", "KE": "Kenia", "KH": "Camboya",
        "KR": "Corea del Sur", "KW": "Kuwait", "KZ": "Kazajistán",
        "LB": "Líbano", "LT": "Lituania", "LU": "Luxemburgo", "LV": "Letonia",
        "MA": "Marruecos", "MK": "Macedonia del Norte", "MT": "Malta",
        "MX": "México", "MY": "Malasia", "NG": "Nigeria", "NL": "Países Bajos",
        "NO": "Noruega", "NP": "Nepal", "NZ": "Nueva Zelanda", "PE": "Perú",
        "PH": "Filipinas", "PK": "Pakistán", "PL": "Polonia", "PT": "Portugal",
        "RO": "Rumania", "RS": "Serbia", "RU": "Rusia", "SA": "Arabia Saudita",
        "SE": "Suecia", "SG": "Singapur", "SI": "Eslovenia", "SK": "Eslovaquia",
        "SV": "El Salvador", "TH": "Tailandia", "TN": "Túnez", "TR": "Turquía",
        "TW": "Taiwán", "UA": "Ucrania", "UK": "Reino Unido", "US": "Estados Unidos",
        "UY": "Uruguay", "VE": "Venezuela", "VN": "Vietnam", "ZA": "Sudáfrica",
    }

    def get_groups(self, content_type: str, country_list: list[str] | None = None) -> list[dict[str, str]]:
        raw = self.content_repo.get_distinct_groups(content_type, country_list)
        return [{"value": g, "label": g} for g in raw]

    def get_countries(self, content_type: str) -> list[dict[str, str]]:
        raw = self.content_repo.get_distinct_countries(content_type)
        return [{"value": c, "label": self.COUNTRY_NAMES.get(c, c)} for c in raw]

    def get_genres(self, content_type: str) -> list[str]:
        return self.content_repo.get_distinct_genres(content_type)

    def get_content_stats(self, content_type: str) -> dict:
        from sqlalchemy import func as sa_func

        metadata_map = {
            "channels": ("total_canales", "channels_generated_at"),
            "movies": ("total_movies", "movies_generated_at"),
            "series": ("total_series", "series_generated_at"),
        }
        total_key, gen_key = metadata_map.get(content_type or "", (None, None))
        if total_key and gen_key:
            total_val = self.sync_meta_repo.get_field(total_key)
            gen_val = self.sync_meta_repo.get_field(gen_key)
            if total_val is not None:
                return {
                    content_type: {
                        "total": int(total_val),
                        "generatedAt": gen_val or "",
                    }
                }

        from app.models.channel import Channel
        from app.models.content import MovieCatalog
        from app.models.series import SeriesCatalog

        model_map = {
            "movies": MovieCatalog,
            "series": SeriesCatalog,
            "channels": Channel,
        }
        model = model_map.get(content_type)
        total = 0
        if model:
            from sqlalchemy import select

            total = self.session.execute(select(sa_func.count()).select_from(model)).scalar() or 0
        from datetime import datetime, timezone

        return {content_type: {"total": total, "generatedAt": datetime.now(timezone.utc).isoformat()}}

    def get_catalog_filters(self, content_type: str, country: str | None = None) -> dict:
        countries = self.get_countries(content_type)
        groups = self.get_groups(content_type, [country] if country else None)
        genres = self.get_genres(content_type) if content_type in ("movies", "series") else []
        return {"countries": countries, "groups": groups, "genres": genres}

    def get_channels_paginated(
        self,
        page: int,
        page_size: int,
        country: str | None = None,
        group: str | None = None,
        search: str | None = None,
    ) -> tuple[list[dict], int]:
        channels, total = self.channel_repo.get_paginated(page, page_size, country, group, search)
        return [
            {
                "id": str(c.id),
                "provider_id": c.provider_id,
                "nombre": c.nombre,
                "nombre_normalizado": c.nombre_normalizado,
                "logo": c.logo,
                "grupo": c.grupo,
                "grupo_normalizado": c.grupo_normalizado,
                "country": c.country,
                "url": c.url,
                "numero": c.numero,
                "tvg_id": c.tvg_id,
            }
            for c in channels
        ], total
