"""
Servicio de conexión a PostgreSQL usando psycopg2
Para consultas SQL directas y complejas

Optimizaciones v2:
- Query get_distinct_series_page: COUNT y datos en una sola query con window function
- Índices recomendados documentados
- get_distinct_groups usa grupo_normalizado (índice más selectivo)
- Pool ampliado a 10 conexiones para carga concurrente
"""
import os
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from contextlib import contextmanager

from utils.config import get_settings


# ============================================================
# ÍNDICES RECOMENDADOS — ejecutar una sola vez en producción
# ============================================================
# CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_series_serie_name
#     ON series(serie_name);
# CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_series_numero
#     ON series(numero);
# CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_series_country
#     ON series(country);
# CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_series_grupo_norm
#     ON series(grupo_normalizado);
#
# Para búsquedas ILIKE %texto% (requiere extensión pg_trgm):
#   CREATE EXTENSION IF NOT EXISTS pg_trgm;
#   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_series_serie_name_trgm
#       ON series USING gin(serie_name gin_trgm_ops);
#   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_series_nombre_norm_trgm
#       ON series USING gin(nombre_normalizado gin_trgm_ops);
#   CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_series_grupo_norm_trgm
#       ON series USING gin(grupo_normalizado gin_trgm_ops);
#
# Igual para channels y movies si experimentan lentitud similar.
# ============================================================


class PostgresService:
    """Servicio para ejecutar consultas SQL directas a PostgreSQL"""

    _pool: Optional[pool.SimpleConnectionPool] = None

    def __init__(self):
        self.settings = get_settings()
        self._connection_string = self._build_connection_string()
        self._init_pool()

    def _init_pool(self):
        """Inicializa el pool de conexiones"""
        if PostgresService._pool is None:
            # Ampliado de 5 a 10 para soportar más concurrencia
            PostgresService._pool = pool.SimpleConnectionPool(
                2, 10,
                self._connection_string
            )

    def _build_connection_string(self) -> str:
        """Construye el string de conexión a PostgreSQL"""
        if self.settings.pg_host:
            return (
                f"postgresql://{self.settings.pg_user}:{self.settings.pg_password}"
                f"@{self.settings.pg_host}:{self.settings.pg_port}/{self.settings.pg_database}"
            )

        supabase_url = self.settings.supabase_url
        if supabase_url:
            host = supabase_url.replace('https://', '').replace('http://', '').rstrip('/')
            if '.supabase.co' in host:
                pg_host = host.replace('.co', '.co:5432')
                return f"postgresql://postgres:{self.settings.supabase_key}@{pg_host}/postgres"

        raise ValueError(
            "No se pudo construir string de conexión a PostgreSQL. "
            "Configura PG_HOST/PG_USER/PG_PASSWORD o SUPABASE_URL/SUPABASE_KEY"
        )

    @contextmanager
    def get_connection(self):
        """Context manager para obtener conexión del pool"""
        conn = None
        try:
            conn = PostgresService._pool.getconn()
            yield conn
        finally:
            if conn:
                PostgresService._pool.putconn(conn)

    def execute_query(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        Ejecuta una consulta SELECT y retorna los resultados.

        Args:
            sql: Consulta SQL a ejecutar
            params: Parámetros para la consulta (previene SQL injection)

        Returns:
            Lista de diccionarios con los resultados
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, params)
                results = cursor.fetchall()
                return [dict(row) for row in results]

    def execute_command(self, sql: str, params: Optional[tuple] = None) -> int:
        """
        Ejecuta un comando SQL (INSERT, UPDATE, DELETE).

        Args:
            sql: Comando SQL a ejecutar
            params: Parámetros para la consulta

        Returns:
            Número de filas afectadas
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                conn.commit()
                return cursor.rowcount

    def get_distinct_groups(self, table: str, countries: Optional[List[str]] = None) -> List[str]:
        """
        Obtiene grupos distintos de una tabla.

        Prioriza grupo_normalizado sobre grupo para mayor consistencia.
        """
        if countries and len(countries) > 0:
            placeholders = ','.join(['%s'] * len(countries))
            sql = f"""
                SELECT DISTINCT COALESCE(NULLIF(grupo_normalizado, ''), grupo) AS grupo
                FROM {table}
                WHERE grupo IS NOT NULL
                  AND grupo != ''
                  AND country IN ({placeholders})
                ORDER BY 1 ASC
            """
            results = self.execute_query(sql, tuple(countries))
        else:
            sql = f"""
                SELECT DISTINCT COALESCE(NULLIF(grupo_normalizado, ''), grupo) AS grupo
                FROM {table}
                WHERE grupo IS NOT NULL
                  AND grupo != ''
                ORDER BY 1 ASC
            """
            results = self.execute_query(sql)

        return [row['grupo'] for row in results if row.get('grupo')]

    def get_distinct_countries(self, table: str) -> List[Dict[str, str]]:
        """
        Obtiene países distintos de una tabla usando GROUP BY.
        """
        sql = f"""
            SELECT country
            FROM {table}
            WHERE country IS NOT NULL AND country != ''
            GROUP BY country
            ORDER BY country ASC
        """
        results = self.execute_query(sql)

        country_names = {
            'AD': 'Andorra', 'AE': 'Emiratos Árabes Unidos', 'AF': 'Afganistán',
            'AL': 'Albania', 'AM': 'Armenia', 'AR': 'Argentina', 'AT': 'Austria',
            'AU': 'Australia', 'AZ': 'Azerbaiyán', 'BE': 'Bélgica', 'BG': 'Bulgaria',
            'BH': 'Baréin', 'BR': 'Brasil', 'BY': 'Bielorrusia', 'CA': 'Canadá',
            'CG': 'República del Congo', 'CH': 'Suiza', 'CY': 'Chipre',
            'CZ': 'República Checa', 'DE': 'Alemania', 'DK': 'Dinamarca',
            'ES': 'España', 'FI': 'Finlandia', 'FR': 'Francia', 'GE': 'Georgia',
            'GR': 'Grecia', 'HK': 'Hong Kong', 'HR': 'Croacia', 'HU': 'Hungría',
            'ID': 'Indonesia', 'IE': 'Irlanda', 'IL': 'Israel', 'IN': 'India',
            'IQ': 'Irak', 'IR': 'Irán', 'IS': 'Islandia', 'IT': 'Italia',
            'JP': 'Japón', 'KA': 'Kazajistán', 'KO': 'Corea del Sur', 'KU': 'Kuwait',
            'LA': 'Laos', 'LT': 'Lituania', 'LU': 'Luxemburgo', 'LV': 'Letonia',
            'MK': 'Macedonia del Norte', 'MT': 'Malta', 'MU': 'Mauricio',
            'MX': 'México', 'MY': 'Malasia', 'NA': 'Namibia', 'NL': 'Países Bajos',
            'NO': 'Noruega', 'NP': 'Nepal', 'NZ': 'Nueva Zelanda', 'PH': 'Filipinas',
            'PK': 'Pakistán', 'PL': 'Polonia', 'PT': 'Portugal', 'RO': 'Rumania',
            'RS': 'Serbia', 'RU': 'Rusia', 'SE': 'Suecia', 'SG': 'Singapur',
            'SI': 'Eslovenia', 'SK': 'Eslovaquia', 'SL': 'Sierra Leona', 'SU': 'Sudán',
            'TH': 'Tailandia', 'TR': 'Turquía', 'TW': 'Taiwán', 'UA': 'Ucrania',
            'UK': 'Reino Unido', 'US': 'Estados Unidos', 'UZ': 'Uzbekistán',
            'VT': 'Vaticano', 'WC': 'Islas Cook', 'ZA': 'Sudáfrica',
        }

        countries = []
        for row in results:
            code = row['country']
            countries.append({'code': code, 'name': country_names.get(code, code)})

        countries.sort(key=lambda c: c['name'])
        return countries

    def get_distinct_series_page(
        self,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Obtiene una página de series únicas.

        Optimizaciones respecto a la versión anterior:
        1. COUNT(*) OVER() — un solo viaje a la base de datos en lugar de dos queries.
        2. Clave de deduplicación computada una sola vez en el CTE base.
        3. DISTINCT ON sobre la clave limpia; ORDER BY alineado para que el planner
           pueda usar el índice idx_series_serie_name directamente.
        4. Params tipados como lista y pasados como tupla en un solo execute.
        """
        filters: List[str] = []
        params: List[Any] = []

        if group:
            filters.append("(grupo_normalizado ILIKE %s OR grupo ILIKE %s)")
            params.extend([f"%{group}%", f"%{group}%"])

        if country:
            filters.append("country = %s")
            params.append(country)

        if search:
            filters.append(
                "(serie_name ILIKE %s OR nombre_normalizado ILIKE %s OR nombre ILIKE %s)"
            )
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        offset = (page - 1) * page_size

        # ── Una sola query: deduplicación + count + paginación ──────────────
        #
        # Flujo:
        #   base      → aplica filtros y calcula series_key una sola vez
        #   deduped   → DISTINCT ON (series_key) ORDER BY series_key, numero
        #               → elige el primer episodio de cada serie como representante
        #   counted   → añade COUNT(*) OVER() para saber el total sin segunda query
        #   resultado → ORDER BY + LIMIT/OFFSET para la página
        #
        # DISTINCT ON exige que la primera clave del ORDER BY coincida con la
        # columna del DISTINCT ON. Si el índice idx_series_serie_name existe,
        # PostgreSQL puede resolver el DISTINCT ON con un IndexScan en lugar de Sort.
        # ────────────────────────────────────────────────────────────────────
        sql = f"""
            WITH base AS (
                SELECT *,
                    COALESCE(
                        NULLIF(serie_name, ''),
                        NULLIF(nombre_normalizado, ''),
                        nombre
                    ) AS series_key
                FROM series
                {where_clause}
            ),
            deduped AS (
                SELECT DISTINCT ON (series_key) *
                FROM base
                ORDER BY series_key ASC, numero ASC
            ),
            counted AS (
                SELECT *, COUNT(*) OVER() AS _total
                FROM deduped
            )
            SELECT *
            FROM counted
            ORDER BY numero ASC
            LIMIT %s OFFSET %s
        """

        all_params = tuple([*params, page_size, offset])
        rows = self.execute_query(sql, all_params)

        total = int(rows[0]['_total']) if rows else 0

        # Limpiar la columna interna antes de devolver
        clean_rows = []
        for row in rows:
            r = dict(row)
            r.pop('_total', None)
            r.pop('series_key', None)
            clean_rows.append(r)

        return {
            'items': clean_rows,
            'total': total,
        }


# Singleton instance
_postgres_service: Optional[PostgresService] = None


def get_postgres_service() -> PostgresService:
    """Obtiene instancia singleton del servicio PostgreSQL"""
    global _postgres_service
    if _postgres_service is None:
        _postgres_service = PostgresService()
    return _postgres_service