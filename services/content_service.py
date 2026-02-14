"""
Servicio de gestión de contenido (canales, películas, series)
Con soporte para paginación estándar y métodos genéricos
"""
import time
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse
from supabase import Client

from utils.config import get_settings
from .postgres_service import get_postgres_service


class ContentService:
    """Servicio para obtener contenido en formato JSON"""

    # Mapeo de tipos de contenido a tablas
    TABLE_MAP = {
        'channels': 'channels',
        'movies': 'movies',
        'series': 'series'
    }

    # Cache estático para countries y groups (TTL: 5 minutos)
    _cache: Dict[str, Tuple[Any, float]] = {}
    _CACHE_TTL_SECONDS = 300

    @classmethod
    def _get_cached(cls, key: str) -> Optional[Any]:
        """Obtiene valor del cache si no ha expirado"""
        if key in cls._cache:
            value, cached_at = cls._cache[key]
            if time.time() - cached_at < cls._CACHE_TTL_SECONDS:
                return value
        return None

    @classmethod
    def _set_cached(cls, key: str, value: Any):
        """Guarda valor en cache con timestamp"""
        cls._cache[key] = (value, time.time())

    # Mapeo de códigos de país a nombres completos
    COUNTRY_NAMES = {
        'AD': 'Andorra',
        'AE': 'Emiratos Árabes Unidos',
        'AF': 'Afganistán',
        'AG': 'Antigua y Barbuda',
        'AI': 'Anguila',
        'AL': 'Albania',
        'AM': 'Armenia',
        'AO': 'Angola',
        'AQ': 'Antártida',
        'AR': 'Argentina',
        'AS': 'Samoa Americana',
        'AT': 'Austria',
        'AU': 'Australia',
        'AW': 'Aruba',
        'AX': 'Islas Åland',
        'AZ': 'Azerbaiyán',
        'BA': 'Bosnia y Herzegovina',
        'BB': 'Barbados',
        'BD': 'Bangladés',
        'BE': 'Bélgica',
        'BF': 'Burkina Faso',
        'BG': 'Bulgaria',
        'BH': 'Baréin',
        'BI': 'Burundi',
        'BJ': 'Benín',
        'BL': 'San Bartolomé',
        'BM': 'Bermudas',
        'BN': 'Brunéi',
        'BO': 'Bolivia',
        'BQ': 'Caribe Neerlandés',
        'BR': 'Brasil',
        'BS': 'Bahamas',
        'BT': 'Bután',
        'BV': 'Isla Bouvet',
        'BW': 'Botsuana',
        'BY': 'Bielorrusia',
        'BZ': 'Belice',
        'CA': 'Canadá',
        'CC': 'Islas Cocos',
        'CD': 'República Democrática del Congo',
        'CF': 'República Centroafricana',
        'CG': 'República del Congo',
        'CH': 'Suiza',
        'CI': 'Costa de Marfil',
        'CK': 'Islas Cook',
        'CL': 'Chile',
        'CM': 'Camerún',
        'CN': 'China',
        'CO': 'Colombia',
        'CR': 'Costa Rica',
        'CU': 'Cuba',
        'CV': 'Cabo Verde',
        'CW': 'Curazao',
        'CX': 'Isla Christmas',
        'CY': 'Chipre',
        'CZ': 'Chequia',
        'DE': 'Alemania',
        'DJ': 'Yibuti',
        'DK': 'Dinamarca',
        'DM': 'Dominica',
        'DO': 'República Dominicana',
        'DZ': 'Argelia',
        'EC': 'Ecuador',
        'EE': 'Estonia',
        'EG': 'Egipto',
        'EH': 'Sáhara Occidental',
        'ER': 'Eritrea',
        'ES': 'España',
        'ET': 'Etiopía',
        'FI': 'Finlandia',
        'FJ': 'Fiyi',
        'FK': 'Islas Malvinas',
        'FM': 'Micronesia',
        'FO': 'Islas Feroe',
        'FR': 'Francia',
        'GA': 'Gabón',
        'GB': 'Reino Unido',
        'GD': 'Granada',
        'GE': 'Georgia',
        'GF': 'Guayana Francesa',
        'GG': 'Guernsey',
        'GH': 'Ghana',
        'GI': 'Gibraltar',
        'GL': 'Groenlandia',
        'GM': 'Gambia',
        'GN': 'Guinea',
        'GP': 'Guadalupe',
        'GQ': 'Guinea Ecuatorial',
        'GR': 'Grecia',
        'GS': 'Islas Georgia del Sur y Sandwich del Sur',
        'GT': 'Guatemala',
        'GU': 'Guam',
        'GW': 'Guinea-Bisáu',
        'GY': 'Guyana',
        'HK': 'Hong Kong',
        'HM': 'Isla Heard y McDonald',
        'HN': 'Honduras',
        'HR': 'Croacia',
        'HT': 'Haití',
        'HU': 'Hungría',
        'ID': 'Indonesia',
        'IE': 'Irlanda',
        'IL': 'Israel',
        'IM': 'Isla de Man',
        'IN': 'India',
        'IO': 'Territorio Británico del Océano Índico',
        'IQ': 'Irak',
        'IR': 'Irán',
        'IS': 'Islandia',
        'IT': 'Italia',
        'JE': 'Jersey',
        'JM': 'Jamaica',
        'JO': 'Jordania',
        'JP': 'Japón',
        'KE': 'Kenia',
        'KG': 'Kirguistán',
        'KH': 'Camboya',
        'KI': 'Kiribati',
        'KM': 'Comoras',
        'KN': 'San Cristóbal y Nieves',
        'KP': 'Corea del Norte',
        'KR': 'Corea del Sur',
        'KW': 'Kuwait',
        'KY': 'Islas Caimán',
        'KZ': 'Kazajistán',
        'LA': 'Laos',
        'LB': 'Líbano',
        'LC': 'Santa Lucía',
        'LI': 'Liechtenstein',
        'LK': 'Sri Lanka',
        'LR': 'Liberia',
        'LS': 'Lesoto',
        'LT': 'Lituania',
        'LU': 'Luxemburgo',
        'LV': 'Letonia',
        'LY': 'Libia',
        'MA': 'Marruecos',
        'MC': 'Mónaco',
        'MD': 'Moldavia',
        'ME': 'Montenegro',
        'MF': 'San Martín',
        'MG': 'Madagascar',
        'MH': 'Islas Marshall',
        'MK': 'Macedonia del Norte',
        'ML': 'Malí',
        'MM': 'Birmania',
        'MN': 'Mongolia',
        'MO': 'Macao',
        'MP': 'Islas Marianas del Norte',
        'MQ': 'Martinica',
        'MR': 'Mauritania',
        'MS': 'Montserrat',
        'MT': 'Malta',
        'MU': 'Mauricio',
        'MV': 'Maldivas',
        'MW': 'Malaui',
        'MX': 'México',
        'MY': 'Malasia',
        'MZ': 'Mozambique',
        'NA': 'Namibia',
        'NC': 'Nueva Caledonia',
        'NE': 'Níger',
        'NF': 'Isla Norfolk',
        'NG': 'Nigeria',
        'NI': 'Nicaragua',
        'NL': 'Países Bajos',
        'NO': 'Noruega',
        'NP': 'Nepal',
        'NR': 'Nauru',
        'NU': 'Niue',
        'NZ': 'Nueva Zelanda',
        'OM': 'Omán',
        'PA': 'Panamá',
        'PE': 'Perú',
        'PF': 'Polinesia Francesa',
        'PG': 'Papúa Nueva Guinea',
        'PH': 'Filipinas',
        'PK': 'Pakistán',
        'PL': 'Polonia',
        'PM': 'San Pedro y Miquelón',
        'PN': 'Islas Pitcairn',
        'PR': 'Puerto Rico',
        'PS': 'Palestina',
        'PT': 'Portugal',
        'PW': 'Palaos',
        'PY': 'Paraguay',
        'QA': 'Catar',
        'RE': 'Reunión',
        'RO': 'Rumania',
        'RS': 'Serbia',
        'RU': 'Rusia',
        'RW': 'Ruanda',
        'SA': 'Arabia Saudita',
        'SB': 'Islas Salomón',
        'SC': 'Seychelles',
        'SD': 'Sudán',
        'SE': 'Suecia',
        'SG': 'Singapur',
        'SH': 'Santa Elena, Ascensión y Tristán de Acuña',
        'SI': 'Eslovenia',
        'SJ': 'Svalbard y Jan Mayen',
        'SK': 'Eslovaquia',
        'SL': 'Sierra Leona',
        'SM': 'San Marino',
        'SN': 'Senegal',
        'SO': 'Somalia',
        'SR': 'Surinam',
        'SS': 'Sudán del Sur',
        'ST': 'Santo Tomé y Príncipe',
        'SV': 'El Salvador',
        'SX': 'Sint Maarten',
        'SY': 'Siria',
        'SZ': 'Esuatini',
        'TC': 'Islas Turcas y Caicos',
        'TD': 'Chad',
        'TF': 'Territorios Australes Franceses',
        'TG': 'Togo',
        'TH': 'Tailandia',
        'TJ': 'Tayikistán',
        'TK': 'Tokelau',
        'TL': 'Timor Oriental',
        'TM': 'Turkmenistán',
        'TN': 'Túnez',
        'TO': 'Tonga',
        'TR': 'Turquía',
        'TT': 'Trinidad y Tobago',
        'TV': 'Tuvalu',
        'TW': 'Taiwán',
        'TZ': 'Tanzania',
        'UA': 'Ucrania',
        'UG': 'Uganda',
        'UM': 'Islas Ultramarinas Menores de Estados Unidos',
        'US': 'Estados Unidos',
        'UY': 'Uruguay',
        'UZ': 'Uzbekistán',
        'VA': 'Ciudad del Vaticano',
        'VC': 'San Vicente y las Granadinas',
        'VE': 'Venezuela',
        'VG': 'Islas Vírgenes Británicas',
        'VI': 'Islas Vírgenes de los Estados Unidos',
        'VN': 'Vietnam',
        'VU': 'Vanuatu',
        'WF': 'Wallis y Futuna',
        'WS': 'Samoa',
        'YE': 'Yemen',
        'YT': 'Mayotte',
        'ZA': 'Sudáfrica',
        'ZM': 'Zambia',
        'ZW': 'Zimbabue',
    }

    def __init__(self, supabase: Client):
        self.supabase = supabase
        self.settings = get_settings()

    def _extract_stream_id(self, url: str) -> tuple:
        """
        Extrae el ID y extensión del stream de la URL original.

        Returns:
            (stream_id, extension, content_type)
        """
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
        """
        Transforma la URL original al formato del proxy.
        """
        if not original_url:
            return ''

        stream_id, extension, content_type = self._extract_stream_id(original_url)

        if not stream_id:
            return ''

        base_url = self.settings.public_domain.rstrip('/')

        # Canales (live) no llevan tipo en la URL, solo movie y series
        if content_type == 'live':
            return f"{base_url}/{username}/{password}/{stream_id}"

        if extension:
            return f"{base_url}/{content_type}/{username}/{password}/{stream_id}.{extension}"
        return f"{base_url}/{content_type}/{username}/{password}/{stream_id}"

    def _parse_content_item(self, row: Dict[str, Any], content_type: str, username: str = '', password: str = '') -> Dict[str, Any]:
        """
        Parsea un item de contenido (canal, película o serie) de Supabase.
        Método genérico que reemplaza a _parse_channel, _parse_movie, _parse_series.
        """
        original_url = row.get('url', '')
        stream_id, extension, content_type_detected = self._extract_stream_id(original_url)

        base_url = self.settings.public_domain.rstrip('/') if username and password else ''
        if original_url and username and password and stream_id:
            # Canales (live) no llevan tipo en la URL, solo movie y series
            if content_type_detected == 'live':
                stream_url = f"{base_url}/{username}/{password}/{stream_id}"
            elif extension:
                stream_url = f"{base_url}/{content_type_detected}/{username}/{password}/{stream_id}.{extension}"
            else:
                stream_url = f"{base_url}/{content_type_detected}/{username}/{password}/{stream_id}"
        else:
            stream_url = None

        base_item = {
            'id': stream_id or '',
            'num': row.get('numero'),
            'nombre': row.get('nombre'),
            'logo': row.get('logo'),
            'grupo': row.get('grupo'),
            'country': row.get('country'),
            'provider_id': row.get('provider_id'),
            'url': original_url,
            'stream_url': stream_url
        }

        # Campos específicos por tipo
        if content_type == 'channels':
            base_item['tvg_id'] = row.get('tvg_id')
        elif content_type == 'series':
            base_item['temporada'] = row.get('temporada')
            base_item['episodio'] = row.get('episodio')

        return base_item

    def _calculate_offset(self, page: int, page_size: int) -> int:
        """Calcula el offset basado en page y page_size"""
        return (page - 1) * page_size

    def _build_base_query(self, table: str, group: Optional[str] = None,
                         country: Optional[str] = None, search: Optional[str] = None,
                         include_count: bool = False):
        """Construye la query base con filtros"""
        count_param = 'exact' if include_count else None
        query = self.supabase.table(table).select('*', count=count_param)

        if group:
            query = query.ilike('grupo', f'%{group}%')
        if country:
            query = query.eq('country', country)
        if search:
            query = query.ilike('nombre', f'%{search}%')

        return query

    def get_content_list(
        self,
        content_type: str,
        page: int = 1,
        page_size: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = '',
        password: str = ''
    ) -> Dict[str, Any]:
        """
        Obtiene lista paginada de contenido (canales, películas o series).
        Método genérico que unifica get_channels, get_movies, get_series.

        Args:
            content_type: 'channels', 'movies' o 'series'
            page: Número de página (1-indexed)
            page_size: Items por página
            group: Filtrar por grupo
            country: Filtrar por país
            search: Buscar por nombre
            username: Usuario para construir URLs de stream
            password: Contraseña para construir URLs de stream
        """
        table = self.TABLE_MAP.get(content_type)
        if not table:
            raise ValueError(f"Tipo de contenido inválido: {content_type}")

        query = self._build_base_query(table, group, country, search, include_count=True)
        query = query.order('numero', desc=False)

        # Calcular rango basado en page y page_size
        offset = self._calculate_offset(page, page_size)
        query = query.range(offset, offset + page_size - 1)

        result = query.execute()

        total = result.count or 0
        pages = (total + page_size - 1) // page_size

        return {
            'items': [self._parse_content_item(row, content_type, username, password) for row in (result.data or [])],
            'total': total,
            'page': page,
            'page_size': page_size,
            'pages': pages,
            'has_next': page < pages,
            'has_prev': page > 1
        }

    def get_content_item(
        self,
        content_type: str,
        item_id: str,
        username: str = '',
        password: str = ''
    ) -> Optional[Dict[str, Any]]:
        """
        Obtiene un item específico de contenido.
        Método genérico que unifica get_channel, get_movie, get_serie.
        """
        table = self.TABLE_MAP.get(content_type)
        if not table:
            raise ValueError(f"Tipo de contenido inválido: {content_type}")

        result = self.supabase.table(table).select('*').eq('id', item_id).execute()

        if result.data:
            return self._parse_content_item(result.data[0], content_type, username, password)
        return None

    # Métodos legacy para compatibilidad (pueden deprecarse gradualmente)
    def get_channels(
        self,
        page: int = 1,
        page_size: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = '',
        password: str = ''
    ) -> Dict[str, Any]:
        """Obtiene lista paginada de canales"""
        return self.get_content_list(
            'channels', page, page_size, group, country, search, username, password
        )

    def get_channel(self, channel_id: str, username: str = '', password: str = '') -> Optional[Dict[str, Any]]:
        """Obtiene un canal específico"""
        return self.get_content_item('channels', channel_id, username, password)

    def get_movies(
        self,
        page: int = 1,
        page_size: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = '',
        password: str = ''
    ) -> Dict[str, Any]:
        """Obtiene lista paginada de películas"""
        return self.get_content_list(
            'movies', page, page_size, group, country, search, username, password
        )

    def get_movie(self, movie_id: str, username: str = '', password: str = '') -> Optional[Dict[str, Any]]:
        """Obtiene una película específica"""
        return self.get_content_item('movies', movie_id, username, password)

    def get_series(
        self,
        page: int = 1,
        page_size: int = 50,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        username: str = '',
        password: str = ''
    ) -> Dict[str, Any]:
        """Obtiene lista paginada de series"""
        return self.get_content_list(
            'series', page, page_size, group, country, search, username, password
        )

    def get_serie(self, series_id: str, username: str = '', password: str = '') -> Optional[Dict[str, Any]]:
        """Obtiene una serie específica"""
        return self.get_content_item('series', series_id, username, password)

    def get_groups(self, content_type: str = 'channels', countries: Optional[List[str]] = None) -> List[str]:
        """Obtiene lista de grupos disponibles, opcionalmente filtrados por países usando PostgreSQL directo"""
        table = self.TABLE_MAP.get(content_type, 'channels')
        cache_key = f"groups:{table}:{','.join(sorted(countries)) if countries else 'all'}"

        # Revisar cache primero
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        # Usar PostgresService para consulta SQL directa con DISTINCT
        pg_service = get_postgres_service()
        result = pg_service.get_distinct_groups(table, countries)

        # Guardar en cache
        self._set_cached(cache_key, result)
        return result

    def get_countries(self, content_type: str = 'channels') -> List[Dict[str, str]]:
        """Obtiene lista de países disponibles con código y nombre usando PostgreSQL directo"""
        table = self.TABLE_MAP.get(content_type, 'channels')
        cache_key = f"countries:{table}"

        # Revisar cache primero
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        # Usar PostgresService para consulta SQL directa con GROUP BY
        pg_service = get_postgres_service()
        result = pg_service.get_distinct_countries(table)

        # Guardar en cache
        self._set_cached(cache_key, result)
        return result

    def get_content_count(self) -> Dict[str, int]:
        """Obtiene el número total de canales, películas y series"""
        channels = self.supabase.table('channels').select('id', count='exact').execute()
        movies = self.supabase.table('movies').select('id', count='exact').execute()
        series = self.supabase.table('series').select('id', count='exact').execute()

        return {
            'channels': channels.count or 0,
            'movies': movies.count or 0,
            'series': series.count or 0
        }

    def get_episodes_by_serie_name(
        self,
        serie_name: str,
        username: str = '',
        password: str = ''
    ) -> List[Dict[str, Any]]:
        """
        Obtiene todos los episodios de una serie por su nombre

        Args:
            serie_name: Nombre de la serie (ej: "Breaking Bad")
            username: Username del usuario para construir stream_url
            password: Password del usuario para construir stream_url

        Returns:
            Lista de episodios ordenados por temporada y episodio
        """
        result = (
            self.supabase.table('series')
            .select('*')
            .eq('serie_name', serie_name)
            .order('temporada')
            .order('episodio')
            .execute()
        )

        episodes = []
        for row in result.data:
            episode = self._parse_content_item(row, 'series', username, password)
            episodes.append(episode)

        return episodes
