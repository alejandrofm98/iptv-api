"""
Servicio de gestión de contenido (canales, películas, series)
Con soporte para paginación estándar y métodos genéricos
"""
import gzip
import json
import logging
import os
import time
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple
import re
from urllib.parse import parse_qs, urlparse
import requests

from utils.config import get_settings
from .postgres_service import PostgresService

logger = logging.getLogger("content_service")


class ContentService:
    """Servicio para obtener contenido en formato JSON"""

    TABLE_MAP = {
        'channels': 'channels',
        'movies': 'movies',
        'series': 'series',
        'replays': 'replays',
    }

    _cache: Dict[str, Tuple[Any, float]] = {}
    _CACHE_TTL_SECONDS = 300

    REPLAY_EMBED_BASE_URL = 'https://dailywrestling.cc/embed'
    REPLAY_METADATA_EMBEDDER = 'https://dailywrestling.cc/'

    @classmethod
    def _get_cached(cls, key: str) -> Optional[Any]:
        if key in cls._cache:
            value, cached_at = cls._cache[key]
            if time.time() - cached_at < cls._CACHE_TTL_SECONDS:
                return value
        return None

    @classmethod
    def _set_cached(cls, key: str, value: Any):
        cls._cache[key] = (value, time.time())

    COUNTRY_NAMES = {
        'AD': 'Andorra', 'AE': 'Emiratos Árabes Unidos', 'AF': 'Afganistán',
        'AG': 'Antigua y Barbuda', 'AI': 'Anguila', 'AL': 'Albania',
        'AM': 'Armenia', 'AO': 'Angola', 'AQ': 'Antártida', 'AR': 'Argentina',
        'AS': 'Samoa Americana', 'AT': 'Austria', 'AU': 'Australia',
        'AW': 'Aruba', 'AX': 'Islas Åland', 'AZ': 'Azerbaiyán',
        'BA': 'Bosnia y Herzegovina', 'BB': 'Barbados', 'BD': 'Bangladés',
        'BE': 'Bélgica', 'BF': 'Burkina Faso', 'BG': 'Bulgaria', 'BH': 'Baréin',
        'BI': 'Burundi', 'BJ': 'Benín', 'BL': 'San Bartolomé', 'BM': 'Bermudas',
        'BN': 'Brunéi', 'BO': 'Bolivia', 'BQ': 'Caribe Neerlandés', 'BR': 'Brasil',
        'BS': 'Bahamas', 'BT': 'Bután', 'BW': 'Botsuana', 'BY': 'Bielorrusia',
        'BZ': 'Belice', 'CA': 'Canadá', 'CC': 'Islas Cocos',
        'CD': 'República Democrática del Congo', 'CF': 'República Centroafricana',
        'CG': 'República del Congo', 'CH': 'Suiza', 'CI': 'Costa de Marfil',
        'CK': 'Islas Cook', 'CL': 'Chile', 'CM': 'Camerún', 'CN': 'China',
        'CO': 'Colombia', 'CR': 'Costa Rica', 'CU': 'Cuba', 'CV': 'Cabo Verde',
        'CW': 'Curazao', 'CX': 'Isla Christmas', 'CY': 'Chipre', 'CZ': 'Chequia',
        'DE': 'Alemania', 'DJ': 'Yibuti', 'DK': 'Dinamarca', 'DM': 'Dominica',
        'DO': 'República Dominicana', 'DZ': 'Argelia', 'EC': 'Ecuador',
        'EE': 'Estonia', 'EG': 'Egipto', 'EH': 'Sáhara Occidental',
        'ER': 'Eritrea', 'ES': 'España', 'ET': 'Etiopía', 'FI': 'Finlandia',
        'FJ': 'Fiyi', 'FK': 'Islas Malvinas', 'FM': 'Micronesia', 'FO': 'Islas Feroe',
        'FR': 'Francia', 'GA': 'Gabón', 'GB': 'Reino Unido', 'GD': 'Granada',
        'GE': 'Georgia', 'GF': 'Guayana Francesa', 'GG': 'Guernsey', 'GH': 'Ghana',
        'GI': 'Gibraltar', 'GL': 'Groenlandia', 'GM': 'Gambia', 'GN': 'Guinea',
        'GP': 'Guadalupe', 'GQ': 'Guinea Ecuatorial', 'GR': 'Grecia',
        'GT': 'Guatemala', 'GU': 'Guam', 'GW': 'Guinea-Bisáu', 'GY': 'Guyana',
        'HK': 'Hong Kong', 'HN': 'Honduras', 'HR': 'Croacia', 'HT': 'Haití',
        'HU': 'Hungría', 'ID': 'Indonesia', 'IE': 'Irlanda', 'IL': 'Israel',
        'IM': 'Isla de Man', 'IN': 'India', 'IO': 'Territorio Británico del Océano Índico',
        'IQ': 'Irak', 'IR': 'Irán', 'IS': 'Islandia', 'IT': 'Italia',
        'JE': 'Jersey', 'JM': 'Jamaica', 'JO': 'Jordania', 'JP': 'Japón',
        'KE': 'Kenia', 'KG': 'Kirguistán', 'KH': 'Camboya', 'KI': 'Kiribati',
        'KM': 'Comoras', 'KN': 'San Cristóbal y Nieves', 'KP': 'Corea del Norte',
        'KR': 'Corea del Sur', 'KW': 'Kuwait', 'KY': 'Islas Caimán',
        'KZ': 'Kazajistán', 'LA': 'Laos', 'LB': 'Líbano', 'LC': 'Santa Lucía',
        'LI': 'Liechtenstein', 'LK': 'Sri Lanka', 'LR': 'Liberia', 'LS': 'Lesoto',
        'LT': 'Lituania', 'LU': 'Luxemburgo', 'LV': 'Letonia', 'LY': 'Libia',
        'MA': 'Marruecos', 'MC': 'Mónaco', 'MD': 'Moldavia', 'ME': 'Montenegro',
        'MF': 'San Martín', 'MG': 'Madagascar', 'MH': 'Islas Marshall',
        'MK': 'Macedonia del Norte', 'ML': 'Malí', 'MM': 'Birmania', 'MN': 'Mongolia',
        'MO': 'Macao', 'MP': 'Islas Marianas del Norte', 'MQ': 'Martinica',
        'MR': 'Mauritania', 'MS': 'Montserrat', 'MT': 'Malta', 'MU': 'Mauricio',
        'MV': 'Maldivas', 'MW': 'Malaui', 'MX': 'México', 'MY': 'Malasia',
        'MZ': 'Mozambique', 'NA': 'Namibia', 'NC': 'Nueva Caledonia', 'NE': 'Níger',
        'NF': 'Isla Norfolk', 'NG': 'Nigeria', 'NI': 'Nicaragua', 'NL': 'Países Bajos',
        'NO': 'Noruega', 'NP': 'Nepal', 'NR': 'Nauru', 'NU': 'Niue',
        'NZ': 'Nueva Zelanda', 'OM': 'Omán', 'PA': 'Panamá', 'PE': 'Perú',
        'PF': 'Polinesia Francesa', 'PG': 'Papúa Nueva Guinea', 'PH': 'Filipinas',
        'PK': 'Pakistán', 'PL': 'Polonia', 'PM': 'San Pedro y Miquelón',
        'PN': 'Islas Pitcairn', 'PR': 'Puerto Rico', 'PS': 'Palestina',
        'PT': 'Portugal', 'PW': 'Palaos', 'PY': 'Paraguay', 'QA': 'Catar',
        'RE': 'Reunión', 'RO': 'Rumania', 'RS': 'Serbia', 'RU': 'Rusia',
        'RW': 'Ruanda', 'SA': 'Arabia Saudita', 'SB': 'Islas Salomón',
        'SC': 'Seychelles', 'SD': 'Sudán', 'SE': 'Suecia', 'SG': 'Singapur',
        'SH': 'Santa Elena, Ascensión y Tristán de Acuña', 'SI': 'Eslovenia',
        'SJ': 'Svalbard y Jan Mayen', 'SK': 'Eslovaquia', 'SL': 'Sierra Leona',
        'SM': 'San Marino', 'SN': 'Senegal', 'SO': 'Somalia', 'SR': 'Surinam',
        'SS': 'Sudán del Sur', 'ST': 'Santo Tomé y Príncipe', 'SV': 'El Salvador',
        'SX': 'Sint Maarten', 'SY': 'Siria', 'SZ': 'Esuatini',
        'TC': 'Islas Turcas y Caicos', 'TD': 'Chad', 'TF': 'Territorios Australes Franceses',
        'TG': 'Togo', 'TH': 'Tailandia', 'TJ': 'Tayikistán', 'TK': 'Tokelau',
        'TL': 'Timor Oriental', 'TM': 'Turkmenistán', 'TN': 'Túnez', 'TO': 'Tonga',
        'TR': 'Turquía', 'TT': 'Trinidad y Tobago', 'TV': 'Tuvalu', 'TW': 'Taiwán',
        'TZ': 'Tanzania', 'UA': 'Ucrania', 'UG': 'Uganda',
        'UM': 'Islas Ultramarinas Menores de Estados Unidos', 'US': 'Estados Unidos',
        'UY': 'Uruguay', 'UZ': 'Uzbekistán', 'VA': 'Ciudad del Vaticano',
        'VC': 'San Vicente y las Granadinas', 'VE': 'Venezuela',
        'VG': 'Islas Vírgenes Británicas', 'VI': 'Islas Vírgenes de los Estados Unidos',
        'VN': 'Vietnam', 'VU': 'Vanuatu', 'WF': 'Wallis y Futuna', 'WS': 'Samoa',
        'YE': 'Yemen', 'YT': 'Mayotte', 'ZA': 'Sudáfica', 'ZM': 'Zambia',
        'ZW': 'Zimbabue',
    }

    def __init__(self, pg_service: PostgresService):
        self.pg = pg_service
        self.settings = get_settings()

    @property
    def _https_base_url(self) -> str:
        """Base URL for stream URLs."""
        return self.settings.public_domain.rstrip('/')

    def _extract_stream_id(self, url: str) -> tuple:
        """Extrae (stream_id, extension, content_type) de la URL original."""
        if not url:
            return (None, None, 'live')

        parsed = urlparse(url)
        path_parts = [p for p in parsed.path.split('/') if p]

        if len(path_parts) >= 2:
            path_lower = parsed.path.lower()

            if '/live/' in path_lower:
                content_type = 'live'
            elif '/movie/' in path_lower:
                content_type = 'movie'
            elif '/series/' in path_lower:
                content_type = 'series'
            else:
                content_type = 'live'

            last_part = path_parts[-1] if path_parts else ''
            if '.' in last_part:
                parts = last_part.rsplit('.', 1)
                stream_id = parts[0]
                extension = parts[1] if len(parts) > 1 else 'ts'
            else:
                stream_id = last_part
                extension = 'ts'

            return (stream_id, extension, content_type)

        return (None, None, 'live')

    def _build_proxy_url(self, original_url: str, username: str, password: str) -> str:
        """Transforma la URL original al formato del proxy."""
        if not original_url:
            return ''

        stream_id, extension, content_type = self._extract_stream_id(original_url)

        if not stream_id:
            return ''

        base_url = self._https_base_url

        if content_type == 'live':
            return f"{base_url}/{username}/{password}/{stream_id}"

        if extension:
            return f"{base_url}/{content_type}/{username}/{password}/{stream_id}.{extension}"
        return f"{base_url}/{content_type}/{username}/{password}/{stream_id}"

    @staticmethod
    def _interpolate_stream_url_template(stream_url: str, username: str, password: str) -> str:
        """Reemplaza placeholders de credenciales en URLs persistidas."""
        if not stream_url:
            return stream_url
        if username:
            stream_url = stream_url.replace('{{USERNAME}}', username)
        if password:
            stream_url = stream_url.replace('{{PASSWORD}}', password)
        return stream_url

    def _build_stream_url(
        self,
        original_url: str,
        persisted_stream_url: Optional[str],
        username: str,
        password: str,
    ) -> Optional[str]:
        if persisted_stream_url:
            return self._interpolate_stream_url_template(persisted_stream_url, username, password)

        if not original_url or not username or not password:
            return None

        stream_id, extension, content_type_detected = self._extract_stream_id(original_url)
        if not stream_id:
            return None

        base_url = self._https_base_url

        if content_type_detected == 'live':
            return f"{base_url}/{username}/{password}/{stream_id}"
        if extension:
            return f"{base_url}/{content_type_detected}/{username}/{password}/{stream_id}.{extension}"
        return f"{base_url}/{content_type_detected}/{username}/{password}/{stream_id}"

    def _parse_content_item(
        self,
        row: Dict[str, Any],
        content_type: str,
        username: str = '',
        password: str = '',
    ) -> Dict[str, Any]:
        """
        Parsea un item de contenido.
        """
        original_url = row.get('url') or ''
        persisted_stream_url = row.get('stream_url') or None

        if persisted_stream_url == '':
            persisted_stream_url = None

        provider_id = row.get('provider_id') or None
        if provider_id:
            stream_id = str(provider_id)
            url_content_type = 'live' if content_type == 'channels' else content_type.rstrip('s')
        else:
            extracted_id, _ext, url_content_type = self._extract_stream_id(original_url)
            stream_id = extracted_id or ''

        internal_id = str(row.get('id') or stream_id)

        if persisted_stream_url and username and password:
            stream_url = self._interpolate_stream_url_template(
                persisted_stream_url, username, password
            )
        elif stream_id and username and password:
            base_url = self._https_base_url
            if url_content_type == 'live':
                stream_url = f"{base_url}/{username}/{password}/{stream_id}"
            else:
                if original_url and '.' in original_url.split('/')[-1]:
                    ext = original_url.split('/')[-1].rsplit('.', 1)[-1]
                else:
                    ext = 'ts'
                stream_url = f"{base_url}/{url_content_type}/{username}/{password}/{stream_id}.{ext}"
        else:
            stream_url = None

        base_item = {
            'id': internal_id,
            'num': row.get('numero'),
            'nombre': row.get('nombre') or '',
            'nombre_normalizado': row.get('nombre_normalizado') or row.get('nombre') or '',
            'logo': row.get('logo') or '',
            'grupo': row.get('grupo') or '',
            'grupo_normalizado': row.get('grupo_normalizado') or row.get('grupo') or '',
            'country': row.get('country'),
            'provider_id': provider_id,
            'url': original_url,
            'stream_url': stream_url,
        }

        if content_type == 'channels':
            base_item['tvg_id'] = row.get('tvg_id')
        elif content_type == 'series':
            base_item['serie_name'] = row.get('serie_name') or ''
            base_item['temporada'] = row.get('temporada')
            base_item['episodio'] = row.get('episodio')

        return base_item

    def _to_android_catalog_item(
        self,
        row: Dict[str, Any],
        content_type: str,
        username: str = '',
        password: str = '',
    ) -> Dict[str, Any]:
        parsed = self._parse_content_item(row, content_type, username, password)
        original_title = parsed.get('nombre') or ''
        original_group = parsed.get('grupo') or ''
        title = parsed.get('nombre_normalizado') or original_title
        group = parsed.get('grupo_normalizado') or original_group

        return {
            'id': parsed.get('provider_id') or parsed.get('id') or '',
            'provider_id': parsed.get('provider_id') or '',
            'type': map_android_type(content_type),
            'title': title,
            'normalized_title': title,
            'original_title': original_title,
            'subtitle': group,
            'description': group,
            'image_url': parsed.get('logo') or '',
            'group': group,
            'normalized_group': group,
            'original_group': original_group,
            'badge_text': (
                group[:8] if content_type == 'channels'
                else ('CINE' if content_type == 'movies' else 'SERIE')
            ),
            'channel_number': parsed.get('num') if content_type == 'channels' else None,
            'language_label': parsed.get('country'),
            'series_name': (
                (parsed.get('serie_name') or parsed.get('nombre_normalizado') or original_title)
                if content_type == 'series' else None
            ),
            'season_number': parsed.get('temporada') if content_type == 'series' else None,
            'episode_number': parsed.get('episodio') if content_type == 'series' else None,
            'stream_url': parsed.get('stream_url') or '',
        }

    def to_android_catalog_item(
        self,
        row: Dict[str, Any],
        content_type: str,
        username: str = '',
        password: str = '',
    ) -> Dict[str, Any]:
        return self._to_android_catalog_item(row, content_type, username, password)

    @staticmethod
    def _catalog_cache_key(
        content_type: str, page: int, page_size: int,
        group: Optional[str], country: Optional[str],
    ) -> str:
        return f"catalog:{content_type}:p{page}:ps{page_size}:g{group}:c{country}"

    def _strip_stream_urls(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Devuelve una copia del resultado sin stream_url para almacenar en caché."""
        stripped_items = []
        for item in result.get('items', []):
            r = dict(item)
            r['stream_url'] = ''
            stripped_items.append(r)
        return {**result, 'items': stripped_items}

    def _inject_stream_urls(
        self, cached: Dict[str, Any], content_type: str, username: str, password: str
    ) -> Dict[str, Any]:
        """Reconstruye stream_url en cada item de un resultado cacheado."""
        if not username or not password:
            return cached

        base_url = self._https_base_url
        injected_items = []
        for item in cached.get('items', []):
            r = dict(item)
            item_id = r.get('id') or ''
            stream_id = r.get('provider_id') or item_id
            if stream_id:
                if content_type == 'channels':
                    r['stream_url'] = f"{base_url}/{username}/{password}/{stream_id}"
                elif content_type == 'movies':
                    r['stream_url'] = f"{base_url}/movie/{username}/{password}/{stream_id}.ts"
                elif content_type == 'series':
                    r['stream_url'] = f"{base_url}/series/{username}/{password}/{stream_id}.ts"
            injected_items.append(r)
        return {**cached, 'items': injected_items}

    def _calculate_offset(self, page: int, page_size: int) -> int:
        return (page - 1) * page_size

    @staticmethod
    def _build_paginated_payload(
        items: List[Dict[str, Any]],
        total: int,
        page: int,
        page_size: int,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        pages = (total + page_size - 1) // page_size if total else 0
        payload = {
            'items': items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'pages': pages,
            'has_next': page < pages,
            'has_prev': page > 1,
        }
        if extra:
            payload.update(extra)
        return payload

    def build_paginated_payload(
        self,
        items: List[Dict[str, Any]],
        total: int,
        page: int,
        page_size: int,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self._build_paginated_payload(items, total, page, page_size, extra)

    def get_content_list(
        self,
        content_type: str,
        page: int = 1,
        page_size: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = '',
        password: str = '',
    ) -> Dict[str, Any]:
        table = self.TABLE_MAP.get(content_type)
        if not table:
            raise ValueError(f"Tipo de contenido inválido: {content_type}")

        if content_type == 'series':
            return self._get_series_catalog_page(
                page=page, page_size=page_size,
                group=group, country=country, search=search,
                username=username, password=password,
            )

        if content_type == 'movies' and not search:
            cache_key = self._catalog_cache_key(content_type, page, page_size, group, country)
            cached = self._get_cached(cache_key)
            if cached is not None:
                return self._inject_stream_urls(cached, content_type, username, password)

        items, total = self.pg.get_content_items_paginated(
            table, page, page_size, group, country, search, 'numero'
        )

        parsed_items = [
            self._parse_content_item(row, content_type, username, password)
            for row in items
        ]

        data = self._build_paginated_payload(parsed_items, total, page, page_size)

        if content_type == 'movies' and not search:
            cache_key = self._catalog_cache_key(content_type, page, page_size, group, country)
            self._set_cached(cache_key, self._strip_stream_urls(data))

        return data

    def _get_series_catalog_page(
        self,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = '',
        password: str = '',
    ) -> Dict[str, Any]:
        use_cache = not search
        cache_key = self._catalog_cache_key('series', page, page_size, group, country)

        if use_cache:
            cached = self._get_cached(cache_key)
            if cached is not None:
                return self._inject_stream_urls(cached, 'series', username, password)

        result = self.pg.get_distinct_series_page(
            page=page, page_size=page_size,
            group=group, country=country, search=search,
        )

        total = result.get('total', 0) or 0
        data = self._build_paginated_payload(
            [
                self._to_android_catalog_item(row, 'series', username, password)
                for row in (result.get('items') or [])
            ],
            total,
            page,
            page_size,
        )

        if use_cache:
            self._set_cached(cache_key, self._strip_stream_urls(data))

        return data

    def get_content_item(
        self,
        content_type: str,
        item_id: str,
        username: str = '',
        password: str = '',
    ) -> Optional[Dict[str, Any]]:
        """
        Obtiene un item específico de contenido.
        Busca primero por id interno, luego por provider_id.
        """
        table = self.TABLE_MAP.get(content_type)
        if not table:
            raise ValueError(f"Tipo de contenido inválido: {content_type}")

        row = self.pg.get_content_item_by_id(table, item_id)
        if not row:
            row = self.pg.get_content_item_by_provider_id(table, item_id)

        if row:
            return self._parse_content_item(row, content_type, username, password)
        return None

    def get_channels(self, page=1, page_size=50, group=None, country=None,
                     search=None, username='', password='') -> Dict[str, Any]:
        return self.get_content_list('channels', page, page_size, group, country, search, username, password)

    def get_channel(self, channel_id: str, username='', password='') -> Optional[Dict[str, Any]]:
        return self.get_content_item('channels', channel_id, username, password)

    def get_movies(self, page=1, page_size=50, group=None, country=None,
                   search=None, username='', password='') -> Dict[str, Any]:
        return self.get_content_list('movies', page, page_size, group, country, search, username, password)

    def get_movie(self, movie_id: str, username='', password='') -> Optional[Dict[str, Any]]:
        return self.get_content_item('movies', movie_id, username, password)

    def get_series(self, page=1, page_size=50, group=None, country=None,
                   search=None, username='', password='') -> Dict[str, Any]:
        return self.get_content_list('series', page, page_size, group, country, search, username, password)

    def get_serie(self, series_id: str, username='', password='') -> Optional[Dict[str, Any]]:
        return self.get_content_item('series', series_id, username, password)

    def get_groups(self, content_type: str = 'channels', countries: Optional[List[str]] = None) -> List[str]:
        table = self.TABLE_MAP.get(content_type, 'channels')
        cache_key = f"groups:{table}:{','.join(sorted(countries)) if countries else 'all'}"

        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        result = self.pg.get_distinct_groups(table, countries)
        self._set_cached(cache_key, result)
        return result

    def get_countries(self, content_type: str = 'channels') -> List[Dict[str, str]]:
        table = self.TABLE_MAP.get(content_type, 'channels')
        cache_key = f"countries:{table}"

        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        result = self.pg.get_distinct_countries(table)
        self._set_cached(cache_key, result)
        return result

    def get_content_count(self) -> Dict[str, int]:
        counts = self.pg.get_content_counts()
        return {
            'channels': counts.get('channels', 0),
            'movies': counts.get('movies', 0),
            'series': counts.get('series', 0),
            'replays': counts.get('replays', 0),
        }

    def get_home_catalog(self, username: str, page_size: int = 12, country: Optional[str] = None, password: str = '') -> Dict[str, Any]:
        counts = self.get_content_count()
        return {
            'featured_channels': self.get_android_home_items('channels', page_size=page_size, username=username, password=password, country=country),
            'featured_movies': self.get_android_home_items('movies', page_size=page_size, username=username, password=password, country=country),
            'featured_series': self.get_android_home_items('series', page_size=page_size, username=username, password=password, country=country),
            'stats': {
                'channels': counts['channels'],
                'movies': counts['movies'],
                'series': counts['series'],
            },
        }

    def get_android_home_items(
        self,
        content_type: str,
        page_size: int = 12,
        username: str = '',
        password: str = '',
        country: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        table = self.TABLE_MAP.get(content_type)
        if not table:
            raise ValueError(f"Tipo de contenido inválido: {content_type}")

        items, _ = self.pg.get_content_items_paginated(
            table, 1, page_size, None, country, None, 'numero'
        )
        return [self._to_android_catalog_item(row, content_type, username, password) for row in items]

    def get_android_content_list(
        self,
        content_type: str,
        page: int = 1,
        page_size: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = '',
        password: str = '',
    ) -> Dict[str, Any]:
        if content_type == 'series':
            return self._get_series_catalog_page(
                page=page, page_size=page_size,
                group=group, country=country, search=search,
                username=username, password=password,
            )

        result = self.get_content_list(
            content_type=content_type,
            page=page, page_size=page_size,
            group=group, country=country, search=search,
            username=username, password=password,
        )
        return {
            **result,
            'items': [
                self._to_android_catalog_item(row, content_type, username, password)
                for row in result['items']
            ],
        }

    def get_catalog_filters(self, content_type: str, country: Optional[str] = None) -> Dict[str, Any]:
        countries = [country] if country else None
        groups = self.get_groups(content_type=content_type, countries=countries)
        if country:
            languages = [country]
        else:
            languages = [item['code'] for item in self.get_countries(content_type=content_type) if item.get('code')]

        return {
            'languages': sorted(set(languages)),
            'groups': groups,
        }

    def search_catalog(
        self,
        query: str,
        types: List[str],
        page: int = 1,
        page_size: int = 50,
        username: str = '',
        password: str = '',
    ) -> Dict[str, Any]:
        requested_types = [ct for ct in types if ct in self.TABLE_MAP]
        if not requested_types:
            requested_types = ['channels', 'movies', 'series']

        merged_items: List[Dict[str, Any]] = []
        for content_type in requested_types:
            table = self.TABLE_MAP[content_type]
            rows = self.pg.search_content(table, query)
            merged_items.extend(
                self._to_android_catalog_item(row, content_type, username, password)
                for row in rows
            )

        merged_items.sort(key=lambda item: item.get('title') or '')
        total = len(merged_items)
        offset = self._calculate_offset(page, page_size)
        paged_items = merged_items[offset:offset + page_size]
        pages = (total + page_size - 1) // page_size if total else 0

        return {
            'items': paged_items,
            'total': total,
            'page': page,
            'page_size': page_size,
            'pages': pages,
            'has_next': page < pages,
            'has_prev': page > 1,
            'types': requested_types,
        }

    def get_episodes_by_serie_name(
        self,
        serie_name: str,
        username: str = '',
        password: str = '',
    ) -> List[Dict[str, Any]]:
        rows = self.pg.get_episodes_by_serie_name(serie_name)
        return [
            self._parse_content_item(row, 'series', username, password)
            for row in rows
        ]

    def get_episodes_by_serie_name_paginated(
        self,
        serie_name: str,
        username: str = '',
        password: str = '',
        page: int = 1,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        rows, total, seasons = self.pg.get_episodes_paginated(serie_name, page, page_size)

        return self._build_paginated_payload(
            [
                self._to_android_catalog_item(row, 'series', username, password)
                for row in rows
            ],
            total,
            page,
            page_size,
            extra={
                'serie_name': serie_name,
                'episodes': rows,
                'total_episodes': total,
                'seasons': seasons,
            },
        )

    def get_replays(
        self,
        page: int = 1,
        page_size: int = 24,
        event_type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        rows, total = self.pg.get_replays_paginated(page, page_size, event_type, search)
        return self._build_paginated_payload(
            [self._parse_replay_item(row) for row in rows],
            total,
            page,
            page_size
        )

    def get_replay(self, slug: str) -> Optional[Dict[str, Any]]:
        row = self.pg.get_replay_by_slug(slug)
        if row:
            return self._parse_replay_item(row)
        return None

    def get_replay_source(self, slug: str, source_index: int, button_index: int) -> Optional[Dict[str, Any]]:
        replay = self.get_replay(slug)
        if not replay:
            return None

        for group in replay.get('video_sources') or []:
            for source in group.get('sources') or []:
                if source.get('source_index') == source_index and source.get('button_index') == button_index:
                    return source

        return None

    def resolve_replay_source_stream_url(
        self,
        slug: str,
        source_index: int,
        button_index: int,
    ) -> Optional[Dict[str, Any]]:
        source = self.get_replay_source(slug, source_index, button_index)
        if not source:
            return None

        provider = str(source.get('provider') or '').lower()
        provider_access_id = source.get('provider_access_id')
        provider_url = source.get('provider_url')
        stream_url = source.get('stream_url')
        stream_format = source.get('stream_format')

        dailymotion_access_id = provider_access_id or self._extract_dailymotion_access_id(
            str(provider_url or stream_url or '')
        )

        if (provider == 'dailymotion' or dailymotion_access_id) and dailymotion_access_id:
            refreshed = self._resolve_dailymotion_stream(str(dailymotion_access_id))
            if refreshed:
                return refreshed

        direct_url = provider_url or stream_url
        if direct_url:
            return {
                'stream_url': direct_url,
                'stream_format': stream_format or self._guess_stream_format(str(direct_url)),
                'provider': provider or self._provider_from_url(str(direct_url)),
            }

        return None

    @staticmethod
    def _parse_replay_item(row: Dict[str, Any]) -> Dict[str, Any]:
        video_sources = ContentService._normalize_replay_sources(
            row.get('video_sources') or [],
            row.get('event_date'),
        )

        return {
            'slug': row.get('slug', ''),
            'source_site': row.get('source_site', ''),
            'title': row.get('title', ''),
            'event_name': row.get('event_name'),
            'event_type': row.get('event_type'),
            'event_date': row.get('event_date'),
            'post_url': row.get('post_url', ''),
            'featured_image_url': row.get('featured_image_url'),
            'description': row.get('description'),
            'video_sources': video_sources,
            'match_card': row.get('match_card') or [],
        }

    @classmethod
    def _normalize_replay_sources(
        cls,
        video_sources: List[Dict[str, Any]],
        event_date: Optional[str],
    ) -> List[Dict[str, Any]]:
        normalized_groups: List[Dict[str, Any]] = []

        for group in video_sources:
            group_name = group.get('group', '')
            sources = []

            for source in (group.get('sources') or []):
                sources.append({
                    'label': source.get('label', ''),
                    'token': source.get('token', ''),
                    'token_enc': source.get('token_enc'),
                    'source_index': source.get('source_index'),
                    'button_index': source.get('button_index'),
                    'embed_url': source.get('embed_url'),
                    'web_embed_url': source.get('web_embed_url'),
                    'provider': source.get('provider'),
                    'provider_url': source.get('provider_url'),
                    'provider_access_id': source.get('provider_access_id'),
                    'provider_video_id': source.get('provider_video_id'),
                    'provider_playlist_id': source.get('provider_playlist_id'),
                    'stream_url': source.get('stream_url'),
                    'stream_format': source.get('stream_format'),
                    'stream_resolved_at': source.get('stream_resolved_at'),
                })

            normalized_groups.append({'group': group_name, 'sources': sources})

        return normalized_groups

    @classmethod
    def _build_replay_embed_url(
        cls,
        category_name: str,
        event_date: Optional[str],
        select_post: int,
        source_index: int,
        button_index: int,
    ) -> Optional[str]:
        if not event_date:
            return None

        try:
            formatted_date = datetime.fromisoformat(str(event_date)).strftime('%m-%d-%Y')
        except ValueError:
            return None

        return (
            f"{cls.REPLAY_EMBED_BASE_URL}/{category_name}/{formatted_date}/select-post-{select_post}/"
            f"{source_index}/{button_index}"
        )

    @staticmethod
    def _reverse_category_name(category_name: str) -> str:
        return str(category_name or '').strip().lower().replace(' ', '')[::-1]

    @classmethod
    def _resolve_dailymotion_stream(cls, provider_access_id: str) -> Optional[Dict[str, Any]]:
        metadata_url = f'https://www.dailymotion.com/player/metadata/video/{provider_access_id}'
        try:
            response = requests.get(
                metadata_url,
                params={'embedder': cls.REPLAY_METADATA_EMBEDDER},
                headers={'User-Agent': 'Mozilla/5.0'},
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        source = cls._pick_best_dailymotion_quality(payload.get('qualities') or {})
        if not source:
            return None

        return {
            'stream_url': source.get('url'),
            'stream_format': source.get('type') or 'application/x-mpegURL',
            'provider': 'dailymotion',
            'provider_video_id': payload.get('id'),
        }

    @staticmethod
    def _pick_best_dailymotion_quality(
        quality_sources: Dict[str, List[Dict[str, Any]]]
    ) -> Optional[Dict[str, Any]]:
        numeric_sources = []
        for label, sources in quality_sources.items():
            if str(label).isdigit() and sources:
                numeric_sources.append((int(str(label)), sources[0]))

        if numeric_sources:
            numeric_sources.sort(key=lambda item: item[0], reverse=True)
            return numeric_sources[0][1]

        auto_sources = quality_sources.get('auto') or []
        if auto_sources:
            return auto_sources[0]

        for sources in quality_sources.values():
            if sources:
                return sources[0]

        return None

    @staticmethod
    def _guess_stream_format(url: str) -> str:
        lowered = url.lower()
        if '.m3u8' in lowered:
            return 'application/x-mpegURL'
        if '.mp4' in lowered:
            return 'video/mp4'
        return 'application/octet-stream'

    @staticmethod
    def _provider_from_url(url: str) -> str:
        parsed = urlparse(url)
        if 'dailymotion' in parsed.netloc or 'dmcdn' in parsed.netloc:
            return 'dailymotion'
        return parsed.netloc or 'unknown'

    @staticmethod
    def _extract_dailymotion_access_id(url: str) -> Optional[str]:
        if not url:
            return None

        patterns = [
            r'/embed/video/([A-Za-z0-9]+)',
            r'/manifest/video/([A-Za-z0-9]+)\.m3u8',
            r'/video/([A-Za-z0-9]+)\.m3u8',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return None

    def get_content_stats(self, content_type: str) -> Dict[str, Any]:
        """Obtiene estadísticas de contenido (total count y generatedAt)."""
        # Intentar leer del archivo JSON estático si existe
        json_data = self._load_static_json(content_type)
        if json_data:
            return {content_type: {'total': json_data.get('total', 0), 'generatedAt': json_data.get('generated_at', '')}}
        
        # Fallback: usar PostgreSQL
        table = self.TABLE_MAP.get(content_type)
        if not table:
            raise ValueError(f"Tipo de contenido inválido: {content_type}")

        total = self.pg.count_table(table)
        return {content_type: {'total': total}}

    def get_all_content_bulk(self, content_type: str) -> Dict[str, Any]:
        """Obtiene TODOS los items de un tipo en una sola llamada desde archivo JSON estático."""
        json_data = self._load_static_json(content_type)
        if json_data:
            return json_data
        
        # Fallback: generar desde PostgreSQL (método antiguo)
        if content_type == 'channels':
            return self._get_all_channels_from_db()
        elif content_type == 'movies':
            return self._get_all_movies_from_db()
        elif content_type == 'series':
            return self._get_all_series_from_db()
        
        raise ValueError(f"Tipo de contenido inválido: {content_type}")

    def get_all_channels_bulk(self) -> Dict[str, Any]:
        """Obtiene TODOS los canales en una sola llamada. Deprecated: usar get_all_content_bulk('channels')"""
        json_data = self._load_static_json('channels')
        if json_data:
            return {'items': json_data.get('channels', []), 'total': json_data.get('total', 0)}
        return self._get_all_channels_from_db()
    
    def _load_static_json(self, content_type: str) -> Optional[Dict[str, Any]]:
        """Carga JSON estático desde disco con cache en memoria."""
        cache_key = f"static_json_{content_type}"
        
        # Verificar cache en memoria
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        
        # Buscar archivo JSON
        json_path = self._get_static_json_path(content_type)
        if not json_path or not os.path.exists(json_path):
            return None
        
        try:
            # Intentar leer versión gzip primero
            gz_path = f"{json_path}.gz"
            if os.path.exists(gz_path):
                with gzip.open(gz_path, 'rt', encoding='utf-8') as f:
                    data = json.load(f)
            else:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            
            # Guardar en cache
            self._set_cached(cache_key, data)
            return data
        except Exception as e:
            logger.error(f"Error cargando JSON estático {content_type}: {e}")
            return None
    
    def _get_static_json_path(self, content_type: str) -> Optional[str]:
        """Obtiene la ruta al archivo JSON estático."""
        # Posibles ubicaciones del archivo JSON
        base_dirs = [
            '/app/data/json',  # Docker
            os.path.join(os.path.dirname(os.path.dirname(__file__)), '..', 'walactv-scrapper', 'data', 'json'),  # Desarrollo local
            'data/json',  # Relative
        ]
        
        filename_map = {
            'channels': 'channels.json',
            'movies': 'movies.json',
            'series': 'series.json',
        }
        
        filename = filename_map.get(content_type)
        if not filename:
            return None
        
        for base_dir in base_dirs:
            path = os.path.join(base_dir, filename)
            if os.path.exists(path):
                return path
        
        return None
    
    def _get_all_channels_from_db(self) -> Dict[str, Any]:
        """Genera JSON de canales desde PostgreSQL (fallback)."""
        rows, _ = self.pg.get_content_items_paginated('channels', 1, 999999, None, None, None, 'numero')

        parsed_items = []
        for row in rows:
            parsed_items.append({
                'id': str(row.get('id') or ''),
                'logo': row.get('logo') or '',
                'provider_id': str(row.get('provider_id') or ''),
                'country': row.get('country') or '',
                'nombre_normalizado': row.get('nombre_normalizado') or row.get('nombre') or '',
                'grupo_normalizado': row.get('grupo_normalizado') or row.get('grupo') or '',
                'numero': row.get('numero'),
            })

        return {
            'items': parsed_items,
            'total': len(parsed_items),
        }
    
    def _get_all_movies_from_db(self) -> Dict[str, Any]:
        """Genera JSON de películas desde PostgreSQL (fallback)."""
        rows, _ = self.pg.get_content_items_paginated('movies', 1, 999999, None, None, None, 'nombre_normalizado')

        parsed_items = []
        for row in rows:
            parsed_items.append({
                'id': str(row.get('id') or ''),
                'provider_id': str(row.get('provider_id') or ''),
                'logo': row.get('logo') or '',
                'country': row.get('country') or '',
                'nombre_normalizado': row.get('nombre_normalizado') or row.get('nombre') or '',
                'grupo_normalizado': row.get('grupo_normalizado') or row.get('grupo') or '',
            })

        return {
            'items': parsed_items,
            'total': len(parsed_items),
        }
    
    def _get_all_series_from_db(self) -> Dict[str, Any]:
        """Genera JSON de series desde PostgreSQL (fallback)."""
        rows, _ = self.pg.get_content_items_paginated('series', 1, 999999, None, None, None, 'nombre_normalizado')

        parsed_items = []
        for row in rows:
            parsed_items.append({
                'id': str(row.get('id') or ''),
                'provider_id': str(row.get('provider_id') or ''),
                'logo': row.get('logo') or '',
                'country': row.get('country') or '',
                'temporada': row.get('temporada'),
                'episodio': row.get('episodio'),
                'serie_name': row.get('serie_name') or '',
                'nombre_normalizado': row.get('nombre_normalizado') or row.get('nombre') or '',
                'grupo_normalizado': row.get('grupo_normalizado') or row.get('grupo') or '',
            })

        return {
            'items': parsed_items,
            'total': len(parsed_items),
        }


def map_android_type(content_type: str) -> str:
    return {
        'channels': 'channel',
        'movies': 'movie',
        'series': 'series',
    }.get(content_type, content_type)