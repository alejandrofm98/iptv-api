"""
Servicio de conexión a PostgreSQL usando psycopg2
Para consultas SQL directas y complejas

Optimizaciones v2:
- Query get_distinct_series_page: COUNT y datos en una sola query con window function
- Índices recomendados documentados
- get_distinct_groups usa grupo_normalizado (índice más selectivo)
- Pool ampliado a 10 conexiones para carga concurrente
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from psycopg2.extras import RealDictCursor, execute_batch
from psycopg2 import pool
from contextlib import contextmanager

from utils.config import get_settings

logger = logging.getLogger("postgres_service")


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
            PostgresService._pool = pool.SimpleConnectionPool(
                2, 10, self._connection_string
            )

    def _build_connection_string(self) -> str:
        """Construye el string de conexión a PostgreSQL"""
        if self.settings.pg_host:
            return (
                f"postgresql://{self.settings.pg_user}:{self.settings.pg_password}"
                f"@{self.settings.pg_host}:{self.settings.pg_port}/{self.settings.pg_database}"
            )

        raise ValueError(
            "No se pudo construir string de conexión a PostgreSQL. "
            "Configura PG_HOST/PG_USER/PG_PASSWORD"
        )

    def _get_valid_order_by_columns(self, table: str) -> List[str]:
        """Retorna columnas válidas para ORDER BY por tabla"""
        common_cols = ["numero", "nombre", "nombre_normalizado", "created_at"]
        table_specific = {
            "movies": ["year"],
            "series": ["year"],
        }
        cols = common_cols + table_specific.get(table, [])
        try:
            sql = "SELECT column_name FROM information_schema.columns WHERE table_name = %s"
            results = self.execute_query(sql, (table,))
            db_cols = [r["column_name"] for r in results]
            return [c for c in cols if c in db_cols]
        except Exception:
            return cols[:1] if cols else ["numero"]

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

    def execute_query(
        self, sql: str, params: Optional[tuple] = None
    ) -> List[Dict[str, Any]]:
        """
        Ejecuta una consulta SELECT y retorna los resultados.
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, params)
                results = cursor.fetchall()
                return [dict(row) for row in results]

    def execute_command(self, sql: str, params: Optional[tuple] = None) -> int:
        """
        Ejecuta un comando SQL (INSERT, UPDATE, DELETE).
        """
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                conn.commit()
                return cursor.rowcount

    def execute_insert(
        self, sql: str, params: Optional[tuple] = None
    ) -> Dict[str, Any]:
        """Ejecuta INSERT y retorna el row insertado usando RETURNING"""
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cursor:
                cursor.execute(sql, params)
                conn.commit()
                result = cursor.fetchone()
                return dict(result) if result else {}

    def bulk_insert(self, table: str, columns: List[str], rows: List[tuple]) -> int:
        """Inserta múltiples filas en una tabla usando execute_batch"""
        if not rows:
            return 0
        placeholders = ",".join(["%s"] * len(columns))
        columns_str = ",".join(columns)
        sql = f"INSERT INTO {table} ({columns_str}) VALUES ({placeholders})"
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                execute_batch(cursor, sql, rows)
                conn.commit()
                return len(rows)

    # ============================================================
    # HELPERS: Users
    # ============================================================

    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """Obtiene usuario por username (incluye password_hash para auth)"""
        sql = "SELECT * FROM users WHERE username = %s"
        results = self.execute_query(sql, (username,))
        return results[0] if results else None

    def get_user_by_id(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene usuario por ID"""
        sql = """
            SELECT id, username, max_connections, is_active, expires_at, created_at, role
            FROM users WHERE id = %s
        """
        results = self.execute_query(sql, (user_id,))
        return results[0] if results else None

    def create_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """Crea un nuevo usuario"""
        sql = """
            INSERT INTO users (username, password_hash, max_connections, is_active, expires_at, role)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, username, max_connections, is_active, expires_at, created_at, role
        """
        return self.execute_insert(
            sql,
            (
                user_data["username"],
                user_data["password_hash"],
                user_data.get("max_connections", 5),
                user_data.get("is_active", True),
                user_data.get("expires_at"),
                user_data.get("role", "user"),
            ),
        )

    def update_user(
        self, user_id: str, user_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Actualiza un usuario y retorna el usuario actualizado"""
        set_clauses = []
        params = []
        for key, value in user_data.items():
            if value is not None and key not in ("id", "username", "created_at"):
                set_clauses.append(f"{key} = %s")
                params.append(value)
        if not set_clauses:
            return self.get_user_by_id(user_id)
        params.append(user_id)
        sql = f"UPDATE users SET {', '.join(set_clauses)} WHERE id = %s RETURNING id, username, max_connections, is_active, expires_at, created_at, role"
        results = self.execute_query(sql, tuple(params))
        return results[0] if results else None

    def delete_user(self, user_id: str) -> bool:
        """Elimina un usuario"""
        sql = "DELETE FROM users WHERE id = %s"
        count = self.execute_command(sql, (user_id,))
        return count > 0

    def list_users(self, skip: int = 0, limit: int = 100) -> List[Dict[str, Any]]:
        """Lista usuarios con paginación"""
        sql = """
            SELECT id, username, max_connections, is_active, expires_at, created_at, role
            FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s
        """
        return self.execute_query(sql, (limit, skip))

    def count_table(self, table: str) -> int:
        """Cuenta registros en una tabla"""
        sql = f"SELECT COUNT(*) as count FROM {table}"
        result = self.execute_query(sql)
        return result[0]["count"] if result else 0

    def get_sync_metadata(self) -> Optional[Dict[str, Any]]:
        """Obtiene metadata de sincronización (totales y generated_at por tipo)."""
        from utils.constants import SYNC_METADATA_TABLE, SYNC_METADATA_ID

        sql = f"SELECT * FROM {SYNC_METADATA_TABLE} WHERE id = %s"
        result = self.execute_query(sql, (SYNC_METADATA_ID,))
        return result[0] if result else None

    def get_sync_metadata_field(self, field: str) -> Optional[str]:
        """Obtiene un campo específico de la tabla sync_metadata."""
        from utils.constants import SYNC_METADATA_TABLE, SYNC_METADATA_ID

        sql = f"SELECT {field} FROM {SYNC_METADATA_TABLE} WHERE id = %s"
        result = self.execute_query(sql, (SYNC_METADATA_ID,))
        if result and result[0].get(field):
            val = result[0][field]
            return val.isoformat() if hasattr(val, "isoformat") else str(val)
        return None

    # ============================================================
    # HELPERS: Sessions
    # ============================================================

    def count_user_sessions(self, user_id: str) -> int:
        """Cuenta sesiones activas de un usuario"""
        sql = "SELECT COUNT(*) as count FROM active_sessions WHERE user_id = %s"
        result = self.execute_query(sql, (user_id,))
        return result[0]["count"] if result else 0

    def get_active_sessions_by_user(self, user_id: str) -> List[Dict[str, Any]]:
        """Obtiene todas las sesiones activas de un usuario"""
        sql = """
            SELECT id, device_id, device_name, device_type, ip_address, last_activity, created_at
            FROM active_sessions WHERE user_id = %s ORDER BY last_activity DESC
        """
        return self.execute_query(sql, (user_id,))

    def get_session_by_user_and_device(
        self, user_id: str, device_id: str
    ) -> Optional[Dict[str, Any]]:
        """Obtiene sesión específica por user_id y device_id"""
        sql = "SELECT * FROM active_sessions WHERE user_id = %s AND device_id = %s"
        results = self.execute_query(sql, (user_id, device_id))
        return results[0] if results else None

    def upsert_session(self, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Inserta o actualiza una sesión"""
        sql = """
            INSERT INTO active_sessions (user_id, device_id, device_name, device_type, ip_address, user_agent, last_activity)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, device_id)
            DO UPDATE SET last_activity = EXCLUDED.last_activity, ip_address = EXCLUDED.ip_address, user_agent = EXCLUDED.user_agent
            RETURNING *
        """
        return self.execute_insert(
            sql,
            (
                session_data["user_id"],
                session_data["device_id"],
                session_data["device_name"],
                session_data["device_type"],
                session_data["ip_address"],
                session_data["user_agent"],
                session_data["last_activity"],
            ),
        )

    def delete_session(self, user_id: str, device_id: str) -> bool:
        """Elimina una sesión específica"""
        sql = "DELETE FROM active_sessions WHERE user_id = %s AND device_id = %s"
        count = self.execute_command(sql, (user_id, device_id))
        return count > 0

    def delete_all_user_sessions(self, user_id: str) -> int:
        """Elimina todas las sesiones de un usuario"""
        sql = "DELETE FROM active_sessions WHERE user_id = %s"
        return self.execute_command(sql, (user_id,))

    def cleanup_inactive_sessions(self, threshold_iso: str) -> int:
        """Limpia sesiones inactivas antes de threshold"""
        sql = "DELETE FROM active_sessions WHERE last_activity < %s"
        return self.execute_command(sql, (threshold_iso,))

    def get_all_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Obtiene todas las sesiones activas"""
        sql = "SELECT * FROM active_sessions ORDER BY last_activity DESC LIMIT %s"
        return self.execute_query(sql, (limit,))

    def get_all_sessions_with_users(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Obtiene todas las sesiones con usernames"""
        sql = """
            SELECT s.*, u.username
            FROM active_sessions s
            LEFT JOIN users u ON s.user_id = u.id
            ORDER BY s.last_activity DESC
            LIMIT %s
        """
        return self.execute_query(sql, (limit,))

    # ============================================================
    # HELPERS: Content
    # ============================================================

    def get_content_item_by_id(
        self, table: str, item_id: str
    ) -> Optional[Dict[str, Any]]:
        """Obtiene item de contenido por ID interno"""
        sql = f"SELECT * FROM {table} WHERE id = %s"
        results = self.execute_query(sql, (item_id,))
        return results[0] if results else None

    def get_content_item_by_provider_id(
        self, table: str, provider_id: str
    ) -> Optional[Dict[str, Any]]:
        """Obtiene item de contenido por provider_id"""
        sql = f"SELECT * FROM {table} WHERE provider_id = %s"
        results = self.execute_query(sql, (provider_id,))
        return results[0] if results else None

    def get_movie_with_metadata(self, movie_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene película con metadata TMDB y stream_options desde catálogo"""
        sql = """
            SELECT mc.*,
                mm.overview_es,
                mm.overview_en,
                mm.vote_average,
                mm.vote_count,
                mm.genres,
                mm.backdrop_path,
                mm.poster_path AS tmdb_poster_path,
                mm.title AS tmdb_title,
                mm.release_date,
                mm.popularity,
                mm.status,
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'url', ms.stream_url,
                            'label', COALESCE(ms.label, ms.country, 'Ver'),
                            'country', ms.country,
                            'provider_id', ms.provider_id,
                            'numero', ms.numero
                        ) ORDER BY
                            CASE WHEN ms.country = 'ES' THEN 0
                                 WHEN ms.country = 'EN' THEN 1
                                 WHEN ms.country = 'LATAM' THEN 2
                                 ELSE 3 END,
                            ms.numero ASC
                    ) FILTER (WHERE ms.id IS NOT NULL),
                    '[]'::jsonb
                ) AS stream_options
            FROM movies_catalog mc
            LEFT JOIN movie_streams ms ON ms.movie_id = mc.id
            LEFT JOIN movies_metadata mm ON mm.provider_id = mc.provider_id
            WHERE mc.id::text = %s
            GROUP BY mc.id, mc.title, mc.provider_id,
                mc.poster_path, mc.backdrop_path, mc.overview_es, mc.overview_en,
                mc.genres, mc.vote_average, mc.vote_count, mc.runtime_minutes,
                mc.release_date, mc.year, mc.tagline, mc.status, mc.popularity,
                mm.overview_es, mm.overview_en, mm.vote_average, mm.vote_count,
                mm.genres, mm.backdrop_path, mm.poster_path, mm.title,
                mm.release_date, mm.popularity, mm.status
            LIMIT 1
        """
        results = self.execute_query(sql, (movie_id,))
        return results[0] if results else None

    def get_series_with_metadata(self, series_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene serie con metadata TMDB desde catálogo"""
        sql = """
            SELECT sc.*,
                sm.overview_es,
                sm.overview_en,
                sm.vote_average,
                sm.vote_count,
                sm.genres,
                sm.backdrop_path,
                sm.poster_path as tmdb_poster_path,
                sm.tagline,
                sm.tmdb_id,
                sm.title as tmdb_title,
                sm.release_date,
                sm.popularity,
                sm.status,
                ep_counts.total_episodes,
                ep_counts.total_seasons
            FROM series_catalog sc
            LEFT JOIN series_metadata sm ON sc.series_key = sm.series_key
            LEFT JOIN (
                SELECT catalog_id,
                    COUNT(DISTINCT id) AS total_episodes,
                    COUNT(DISTINCT season_number) AS total_seasons
                FROM series_episodes
                GROUP BY catalog_id
            ) ep_counts ON ep_counts.catalog_id = sc.id
            WHERE sc.id::text = %s OR sc.series_key = %s
            LIMIT 1
        """
        results = self.execute_query(sql, (series_id, series_id))
        return results[0] if results else None

    def get_content_items_paginated(
        self,
        table: str,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        upper_group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        order_by: str = "year",
        year: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Obtiene items de contenido con paginación y filtros"""
        filters = []
        params = []

        if group:
            filters.append("(grupo_normalizado ILIKE %s OR grupo ILIKE %s)")
            params.extend([f"%{group}%", f"%{group}%"])

        if upper_group:
            filters.append("UPPER(grupo_normalizado) LIKE %s")
            params.append(f"%{upper_group}%")

        if country:
            filters.append("country = %s")
            params.append(country)

        if search:
            filters.append("(nombre_normalizado ILIKE %s OR nombre ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if year:
            filters.append("year = %s")
            params.append(year)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        # Count simple sin DISTINCT
        count_sql = f"SELECT COUNT(*) as total FROM {table} {where_clause}"

        count_result = self.execute_query(count_sql, tuple(params))
        total = count_result[0]["total"] if count_result else 0

        offset = (page - 1) * page_size
        valid_cols = self._get_valid_order_by_columns(table)
        order_col = (
            order_by
            if order_by in valid_cols
            else valid_cols[0]
            if valid_cols
            else "numero"
        )
        data_sql = f"""
            SELECT * FROM {table}
            {where_clause}
            ORDER BY {order_col} DESC NULLS LAST
            LIMIT %s OFFSET %s
        """
        data_params = tuple([*params, page_size, offset])
        items = self.execute_query(data_sql, data_params)

        return items, total

    def get_distinct_channels_page(
        self,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        upper_group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Obtiene canales deduplicados por nombre_normalizado con paginación usando ROW_NUMBER()"""
        filters = []
        params = []

        if group:
            filters.append("(grupo_normalizado ILIKE %s OR grupo ILIKE %s)")
            params.extend([f"%{group}%", f"%{group}%"])

        if upper_group:
            filters.append("UPPER(grupo_normalizado) LIKE %s")
            params.append(f"%{upper_group}%")

        if country:
            filters.append("country = %s")
            params.append(country)

        if search:
            filters.append("(nombre_normalizado ILIKE %s OR nombre ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        dedup_key_expr = """
            COALESCE(
                NULLIF(nombre_dedup_key, ''),
                LOWER(NULLIF(nombre_normalizado, '')),
                LOWER(nombre)
            )
        """

        count_sql = f"""
            SELECT COUNT(DISTINCT {dedup_key_expr}) as total
            FROM channels
            {where_clause}
        """
        count_result = self.execute_query(count_sql, tuple(params))
        total = count_result[0]["total"] if count_result else 0

        start_row = (page - 1) * page_size + 1
        end_row = page * page_size

        data_sql = f"""
            WITH base AS (
                SELECT *,
                {dedup_key_expr} AS dedup_key
                FROM channels
                {where_clause}
            ),
            deduped AS (
                SELECT DISTINCT ON (dedup_key) *
                FROM base
                ORDER BY dedup_key ASC, numero ASC NULLS LAST
            ),
            numbered AS (
                SELECT *,
                ROW_NUMBER() OVER (ORDER BY numero ASC NULLS LAST, nombre_normalizado ASC, id ASC) as rn
                FROM deduped
            )
            SELECT * FROM numbered
            WHERE rn BETWEEN %s AND %s
            ORDER BY rn
        """
        data_params = tuple([*params, start_row, end_row])
        items = self.execute_query(data_sql, data_params)

        for item in items:
            item.pop("rn", None)

        return items, total

    def get_watched_items(self, user_id: str, limit: int = 100):
        sql = """
            SELECT * \
            FROM watch_progress
            WHERE user_id = %s \
              AND is_watched = TRUE
            ORDER BY last_watched_at DESC
                LIMIT %s \
            """
        return self.execute_query(sql, (user_id, limit))

    def search_content(self, table: str, query: str) -> List[Dict[str, Any]]:
        """Busca contenido por nombre"""
        sql = f"""
            SELECT * FROM {table}
            WHERE nombre_normalizado ILIKE %s OR nombre ILIKE %s
            ORDER BY numero ASC
        """
        return self.execute_query(sql, (f"%{query}%", f"%{query}%"))

    def get_content_counts(self) -> Dict[str, int]:
        """Obtiene conteo de contenido desde tablas catálogo + flat channels/replays"""
        return {
            "channels": self.count_table("channels"),
            "movies": self.count_catalog_movies(),
            "series": self.count_catalog_series(),
            "replays": self.count_table("replays"),
        }

    def count_catalog_movies(self) -> int:
        sql = "SELECT COUNT(*) as count FROM movies_catalog"
        result = self.execute_query(sql)
        return result[0]["count"] if result else 0

    def count_catalog_series(self) -> int:
        sql = "SELECT COUNT(*) as count FROM series_catalog"
        result = self.execute_query(sql)
        return result[0]["count"] if result else 0

    def get_channels_by_provider_ids(self, provider_ids: List) -> List[Dict[str, Any]]:
        """Obtiene canales por lista de provider_ids"""
        if not provider_ids:
            return []
        placeholders = ",".join(["%s"] * len(provider_ids))
        sql = f"SELECT * FROM channels WHERE provider_id::text IN ({placeholders})"
        return self.execute_query(sql, tuple(provider_ids))

    def get_all_content_urls(self, table: str) -> List[Dict[str, Any]]:
        """Obtiene todos los IDs y URLs de una tabla para precargar cache"""
        sql = f"SELECT id, provider_id, url FROM {table} WHERE url IS NOT NULL AND url != ''"
        return self.execute_query(sql)

    # ============================================================
    # HELPERS: Series
    # ============================================================

    # NOTA: get_episodes_by_serie_name, get_episodes_paginated, get_series_seasons,
    # get_series_groups_page, get_distinct_series_page han sido reemplazados por métodos
    # de catálogo. Ver get_series_episodes_grouped y get_distinct_series_groups_catalog.

    # ============================================================
    # HELPERS: Catálogo normalizado (series_catalog / movies_catalog)
    # ============================================================

    def get_series_catalog_by_key(self, series_key: str) -> Optional[Dict[str, Any]]:
        """Obtiene entrada del catálogo de series por series_key"""
        sql = """
            SELECT sc.*,
               sm.overview_es, sm.overview_en, sm.vote_average, sm.vote_count,
               sm.genres, sm.backdrop_path, sm.poster_path, sm.title AS tmdb_title,
               sm.release_date, sm.popularity, sm.status
            FROM series_catalog sc
            LEFT JOIN series_metadata sm ON sc.series_key = sm.series_key
            WHERE sc.series_key = %s
            LIMIT 1
        """
        results = self.execute_query(sql, (series_key,))
        return results[0] if results else None

    def get_series_catalog_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        """Resuelve un título de serie (posiblemente con prefijo de idioma) al catálogo"""
        stripped = re.sub(r'^[a-z]{2,5}\s*[-–]\s*', '', title, flags=re.IGNORECASE).strip()
        sql = """
            SELECT sc.*,
               sm.overview_es, sm.overview_en, sm.vote_average, sm.vote_count,
               sm.genres, sm.backdrop_path, sm.poster_path, sm.title AS tmdb_title,
               sm.release_date, sm.popularity, sm.status
            FROM series_catalog sc
            LEFT JOIN series_metadata sm ON sc.series_key = sm.series_key
            WHERE LOWER(TRIM(sc.title)) IN (LOWER(TRIM(%s)), LOWER(TRIM(%s)))
               OR LOWER(TRIM(sc.title)) LIKE LOWER(TRIM(%s))
            LIMIT 1
        """
        results = self.execute_query(sql, (title, stripped, f"%{stripped}%"))
        return results[0] if results else None

    def get_series_episodes_grouped(
        self,
        catalog_id: str,
        page: int = 1,
        page_size: int = 100,
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Devuelve episodios de una serie con TODOS sus stream_options agrupados.
        Cada episodio tiene un array 'stream_options' con variantes de idioma/calidad.
        """
        offset = (page - 1) * page_size
        count_sql = """
            SELECT COUNT(*) as total
            FROM series_episodes
            WHERE catalog_id = %s
        """
        count_result = self.execute_query(count_sql, (catalog_id,))
        total = count_result[0]["total"] if count_result else 0

        seasons_sql = """
            SELECT DISTINCT season_number
            FROM series_episodes
            WHERE catalog_id = %s
            ORDER BY season_number
        """
        seasons_result = self.execute_query(seasons_sql, (catalog_id,))
        seasons = [r["season_number"] for r in seasons_result]

        data_sql = """
            WITH episode_streams AS (
                SELECT
                    se.id AS episode_id,
                    se.season_number,
                    se.episode_number,
                    se.numero,
                    jsonb_agg(
                        jsonb_build_object(
                            'url', ss.stream_url,
                            'label', COALESCE(ss.label, ss.country, 'Ver'),
                            'country', ss.country,
                            'provider_id', ss.provider_id,
                            'numero', ss.numero
                        ) ORDER BY
                            CASE WHEN ss.country = 'ES' THEN 0
                                 WHEN ss.country = 'EN' THEN 1
                                 WHEN ss.country = 'LATAM' THEN 2
                                 ELSE 3 END,
                            ss.numero ASC
                    ) AS stream_options
                FROM series_episodes se
                LEFT JOIN series_streams ss ON ss.episode_id = se.id
                WHERE se.catalog_id = %s
                GROUP BY se.id, se.season_number, se.episode_number, se.numero
            )
            SELECT *
            FROM episode_streams
            ORDER BY season_number ASC, episode_number ASC
            LIMIT %s OFFSET %s
        """
        items = self.execute_query(data_sql, (catalog_id, page_size, offset))
        return items, total, seasons

    def get_movies_catalog_page(
        self,
        page: int = 1,
        page_size: int = 24,
        group: Optional[str] = None,
        upper_group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        order_by: str = "year",
        year: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Reemplaza get_distinct_movies_page usando movies_catalog + movie_streams.
        Devuelve películas con stream_options agrupados (todos los idiomas/calidades).
        """
        filters: List[str] = []
        params: List[Any] = []

        if group:
            filters.append("(mc.group_normalizado ILIKE %s OR mc.title ILIKE %s)")
            params.extend([f"%{group}%", f"%{group}%"])

        if upper_group:
            filters.append("UPPER(mc.group_normalizado) LIKE %s")
            params.append(f"%{upper_group}%")

        if country:
            filters.append("mc.country = %s")
            params.append(country)

        if search:
            filters.append("(mc.title ILIKE %s OR mc.tmdb_id::text ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if year:
            filters.append("mc.year = %s")
            params.append(year)

        order_col = "year" if order_by == "year" else "title"
        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        offset = (page - 1) * page_size

        count_sql = f"""
            SELECT COUNT(DISTINCT mc.id) as total
            FROM movies_catalog mc
            {where_clause}
        """
        count_result = self.execute_query(count_sql, params)
        total = count_result[0]["total"] if count_result else 0

        data_sql = f"""
            WITH movie_options AS (
                SELECT
                    mc.id,
                    mc.title,
                    mc.tmdb_id,
                    mc.poster_path,
                    mc.backdrop_path,
                    mc.overview_es,
                    mc.overview_en,
                    mc.genres,
                    mc.vote_average,
                    mc.vote_count,
                    mc.runtime_minutes,
                    mc.release_date,
                    mc.year,
                    mc.tagline,
                    mc.status,
                    mc.popularity,
                    mc.country,
                    mc.group_normalizado,
                    mc.logo,
                    mc.numero,
                    mc.provider_id,
                    mm.overview_es AS mm_overview_es,
                    mm.overview_en AS mm_overview_en,
                    mm.vote_average AS mm_vote_average,
                    mm.vote_count AS mm_vote_count,
                    mm.genres AS mm_genres,
                    mm.backdrop_path AS mm_backdrop,
                    mm.poster_path AS mm_poster,
                    mm.title AS tmdb_title,
                    mm.release_date AS mm_release_date,
                    mm.popularity AS mm_popularity,
                    mm.status AS mm_status,
                    jsonb_agg(
                        jsonb_build_object(
                            'url', ms.stream_url,
                            'label', COALESCE(ms.label, ms.country, 'Ver'),
                            'country', ms.country,
                            'provider_id', ms.provider_id,
                            'numero', ms.numero
                        ) ORDER BY
                            CASE WHEN ms.country = 'ES' THEN 0
                                 WHEN ms.country = 'EN' THEN 1
                                 WHEN ms.country = 'LATAM' THEN 2
                                 ELSE 3 END,
                            ms.numero ASC
                    ) AS stream_options,
                    COUNT(ms.id) AS stream_count
                FROM movies_catalog mc
                LEFT JOIN movie_streams ms ON ms.movie_id = mc.id
                LEFT JOIN movies_metadata mm ON mm.provider_id = mc.provider_id
                {where_clause}
                GROUP BY mc.id, mc.title, mc.provider_id,
                    mc.poster_path, mc.backdrop_path, mc.overview_es, mc.overview_en,
                    mc.genres, mc.vote_average, mc.vote_count, mc.runtime_minutes,
                    mc.release_date, mc.year, mc.tagline, mc.status, mc.popularity,
                    mc.country, mc.group_normalizado, mc.logo, mc.numero, mc.provider_id,
                    mm.overview_es, mm.overview_en, mm.vote_average, mm.vote_count,
                    mm.genres, mm.backdrop_path, mm.poster_path, mm.title,
                    mm.release_date, mm.popularity, mm.status
            )
            SELECT *
            FROM movie_options
            ORDER BY {order_col} DESC NULLS LAST
            LIMIT %s OFFSET %s
        """
        all_params = params + [page_size, offset]
        items = self.execute_query(data_sql, all_params)
        return items, total

    def get_movie_catalog_with_streams(self, catalog_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene una película del catálogo con todos sus stream_options"""
        sql = """
            SELECT
                mc.*,
                mm.overview_es AS mm_overview_es,
                mm.overview_en AS mm_overview_en,
                mm.vote_average AS mm_vote_average,
                mm.vote_count AS mm_vote_count,
                mm.genres AS mm_genres,
                mm.backdrop_path AS mm_backdrop,
                mm.poster_path AS mm_poster,
                mm.title AS tmdb_title,
                mm.release_date AS mm_release_date,
                mm.popularity AS mm_popularity,
                mm.status AS mm_status,
                COALESCE(
                    jsonb_agg(
                        jsonb_build_object(
                            'url', ms.stream_url,
                            'label', COALESCE(ms.label, ms.country, 'Ver'),
                            'country', ms.country,
                            'provider_id', ms.provider_id,
                            'numero', ms.numero
                        ) ORDER BY
                            CASE WHEN ms.country = 'ES' THEN 0
                                 WHEN ms.country = 'EN' THEN 1
                                 WHEN ms.country = 'LATAM' THEN 2
                                 ELSE 3 END,
                            ms.numero ASC
                    ) FILTER (WHERE ms.id IS NOT NULL),
                    '[]'::jsonb
                ) AS stream_options
            FROM movies_catalog mc
            LEFT JOIN movie_streams ms ON ms.movie_id = mc.id
            LEFT JOIN movies_metadata mm ON mm.provider_id = mc.provider_id
            WHERE mc.id = %s
            GROUP BY mc.id, mc.title, mc.provider_id,
                mc.poster_path, mc.backdrop_path, mc.overview_es, mc.overview_en,
                mc.genres, mc.vote_average, mc.vote_count, mc.runtime_minutes,
                mc.release_date, mc.year, mc.tagline, mc.status, mc.popularity,
                mm.overview_es, mm.overview_en, mm.vote_average, mm.vote_count,
                mm.genres, mm.backdrop_path, mm.poster_path, mm.title,
                mm.release_date, mm.popularity, mm.status
            LIMIT 1
        """
        results = self.execute_query(sql, (catalog_id,))
        return results[0] if results else None

    def get_distinct_series_groups_catalog(self, page: int, page_size: int, group: Optional[str] = None,
        upper_group: Optional[str] = None, country: Optional[str] = None, search: Optional[str] = None,
        year: Optional[int] = None) -> Dict[str, Any]:
        """
        Versión para catálogo normalizado: devuelve series agrupadas con total de episodios.
        Solo consulta series_catalog + series_episodes + series_metadata (sin tablas planas).
        """
        filters: List[str] = []
        params: List[Any] = []

        if group:
            filters.append("(sc.group_normalizado ILIKE %s OR sc.title ILIKE %s)")
            params.extend([f"%{group}%", f"%{group}%"])

        if upper_group:
            filters.append("UPPER(sc.group_normalizado) LIKE %s")
            params.append(f"%{upper_group}%")

        if country:
            filters.append("sc.country = %s")
            params.append(country)

        if search:
            filters.append("(sc.title ILIKE %s OR sc.title ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if year:
            filters.append("sc.year = %s")
            params.append(year)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        offset = (page - 1) * page_size

        count_sql = f"""
            SELECT COUNT(DISTINCT sc.id) as total
            FROM series_catalog sc
            {where_clause}
        """
        count_result = self.execute_query(count_sql, params)
        total = count_result[0]["total"] if count_result else 0

        data_sql = f"""
            SELECT
                sc.id,
                sc.title,
                sc.series_key,
                sc.tmdb_id,
                sc.poster_path,
                sc.backdrop_path,
                sc.overview_es,
                sc.overview_en,
                sc.genres,
                sc.vote_average,
                sc.vote_count,
                sc.year,
                sc.status,
                sc.popularity,
                sc.group_normalizado,
                sc.country,
                sc.logo,
                sc.numero,
                sc.provider_id,
                sm.overview_es AS sm_overview_es,
                sm.overview_en AS sm_overview_en,
                sm.vote_average AS sm_vote_average,
                sm.vote_count AS sm_vote_count,
                sm.genres AS sm_genres,
                sm.backdrop_path AS sm_backdrop,
                sm.poster_path AS sm_poster,
                sm.title AS tmdb_title,
                sm.release_date AS sm_release_date,
                sm.popularity AS sm_popularity,
                sm.status AS sm_status,
                COUNT(DISTINCT se.id) AS total_episodes,
                COUNT(DISTINCT se.season_number) AS total_seasons
            FROM series_catalog sc
            LEFT JOIN series_metadata sm ON sc.series_key = sm.series_key
            LEFT JOIN series_episodes se ON se.catalog_id = sc.id
            {where_clause}
            GROUP BY sc.id, sc.title, sc.series_key, sc.tmdb_id,
                sc.poster_path, sc.backdrop_path, sc.overview_es, sc.overview_en,
                sc.genres, sc.vote_average, sc.vote_count, sc.year,
                sc.status, sc.popularity, sc.group_normalizado, sc.country,
                sc.logo, sc.numero, sc.provider_id,
                sm.overview_es, sm.overview_en, sm.vote_average, sm.vote_count,
                sm.genres, sm.backdrop_path, sm.poster_path, sm.title,
                sm.release_date, sm.popularity, sm.status
            ORDER BY sc.title ASC
            LIMIT %s OFFSET %s
        """
        all_params = params + [page_size, offset]
        items = self.execute_query(data_sql, all_params)

        return {"items": items, "total": total, "page": page, "page_size": page_size}

    # ============================================================
    # HELPERS: Replays
    # ============================================================

    def get_replays_paginated(
        self,
        page: int,
        page_size: int,
        event_type: Optional[str] = None,
        search: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Obtiene replays con paginación y filtros"""
        filters = []
        params = []

        if event_type:
            filters.append("event_type = %s")
            params.append(event_type)

        if search:
            filters.append(
                "(title ILIKE %s OR event_name ILIKE %s OR description ILIKE %s)"
            )
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""

        count_sql = f"SELECT COUNT(*) as total FROM replays {where_clause}"
        count_result = self.execute_query(count_sql, tuple(params))
        total = count_result[0]["total"] if count_result else 0

        offset = (page - 1) * page_size
        data_sql = f"""
            SELECT * FROM replays
            {where_clause}
            ORDER BY event_date DESC, created_at DESC
            LIMIT %s OFFSET %s
        """
        data_params = tuple([*params, page_size, offset])
        items = self.execute_query(data_sql, data_params)

        return items, total

    def get_replay_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Obtiene replay por slug"""
        sql = "SELECT * FROM replays WHERE slug = %s"
        results = self.execute_query(sql, (slug,))
        return results[0] if results else None

    # ============================================================
    # HELPERS: Watch Progress
    # ============================================================

    def get_watch_progress_rows(
        self, user_id: str, content_id: str
    ) -> List[Dict[str, Any]]:
        """Busca registros de watch_progress por user_id y content_id (con soporte para IDs legacy movie:/series:)"""
        base_id = content_id.split(":", 1)[1] if ":" in content_id else content_id
        candidates = [content_id, base_id]
        if ":" not in content_id and base_id.isdigit():
            candidates.extend([f"movie:{base_id}", f"series:{base_id}"])

        results = []
        for candidate in candidates:
            sql = "SELECT * FROM watch_progress WHERE user_id = %s AND content_id = %s"
            rows = self.execute_query(sql, (user_id, candidate))
            results.extend(rows)

        unique = {}
        for row in results:
            key = str(row.get("id") or row.get("content_id"))
            unique[key] = row
        return list(unique.values())

    def get_continue_watching(
        self, user_id: str, limit: int = 20
    ) -> List[Dict[str, Any]]:
        """Obtiene items con progreso incompleto (entre 5% y 95%)"""
        sql = """
            SELECT * FROM watch_progress
            WHERE user_id = %s AND position_ms > 0
            ORDER BY last_watched_at DESC
            LIMIT %s
        """
        return self.execute_query(sql, (user_id, limit))

    def upsert_watch_progress(
        self, user_id: str, content_id: str, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Inserta o actualiza watch_progress"""
        position_ms = data.get("position_ms", 0)
        duration_ms = data.get("duration_ms", 0)

        # Auto-mark como visto si >= 95%
        is_watched = data.get("is_watched", False)
        if not is_watched and duration_ms > 0 and position_ms >= duration_ms * 95 / 100:
            is_watched = True

        sql = """
            INSERT INTO watch_progress (user_id, content_id, content_type, position_ms, duration_ms, series_name, season_number, episode_number, title, image_url, last_watched_at, is_watched)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, content_id)
            DO UPDATE SET
                position_ms = EXCLUDED.position_ms,
                duration_ms = EXCLUDED.duration_ms,
                series_name = EXCLUDED.series_name,
                season_number = EXCLUDED.season_number,
                episode_number = EXCLUDED.episode_number,
                title = EXCLUDED.title,
                image_url = EXCLUDED.image_url,
                last_watched_at = EXCLUDED.last_watched_at,
                is_watched = EXCLUDED.is_watched
            RETURNING *
        """
        return self.execute_insert(
            sql,
            (
                user_id,
                content_id,
                data.get("content_type"),
                position_ms,
                duration_ms,
                data.get("series_name"),
                data.get("season_number"),
                data.get("episode_number"),
                data.get("title", ""),
                data.get("image_url", ""),
                data.get("last_watched_at"),
                is_watched,
            ),
        )

    def update_is_watched(
        self, user_id: str, content_id: str, is_watched: bool
    ) -> bool:
        """Marca o desmarca como visto un contenido"""
        sql = """
            UPDATE watch_progress 
            SET is_watched = %s, last_watched_at = NOW()
            WHERE user_id = %s AND content_id = %s
            RETURNING is_watched
        """
        rows = self.execute_query(sql, (is_watched, user_id, content_id))
        return rows[0].get("is_watched") if rows else None

    def get_series_last_episode(
        self, user_id: str, series_name: str
    ) -> Optional[Dict[str, Any]]:
        """Obtiene el último episodio visto de una serie"""
        sql = """
            SELECT * FROM watch_progress
            WHERE user_id = %s AND series_name = %s AND content_type = 'series'
            ORDER BY season_number DESC NULLS LAST, episode_number DESC NULLS LAST
            LIMIT 1
        """
        rows = self.execute_query(sql, (user_id, series_name))
        return rows[0] if rows else None

    def is_series_complete(self, user_id: str, series_name: str) -> bool:
        """Verifica si la serie está completa (último episodio visto)"""
        last_ep = self.get_series_last_episode(user_id, series_name)
        if not last_ep:
            return False
        # Si el último episodio tiene is_watched = True, la serie está completa
        return last_ep.get("is_watched", False)

    def delete_watch_progress(self, user_id: str, content_id: str) -> bool:
        """Elimina watch_progress"""
        sql = "DELETE FROM watch_progress WHERE user_id = %s AND content_id = %s"
        count = self.execute_command(sql, (user_id, content_id))
        return count > 0

    # ============================================================
    # HELPERS: Favorites
    # ============================================================

    def list_favorites(self, user_id: str) -> List[Dict[str, Any]]:
        """Lista favoritos de un usuario"""
        sql = """
            SELECT user_id, channel_provider_id, created_at
            FROM channel_favorites
            WHERE user_id = %s
            ORDER BY created_at DESC
        """
        return self.execute_query(sql, (user_id,))

    def add_favorite(self, user_id: str, channel_provider_id: str) -> Dict[str, Any]:
        """Agrega o actualiza un favorito"""
        sql = """
            INSERT INTO channel_favorites (user_id, channel_provider_id)
            VALUES (%s, %s)
            ON CONFLICT (user_id, channel_provider_id) DO NOTHING
            RETURNING user_id, channel_provider_id, created_at
        """
        return self.execute_insert(sql, (user_id, str(channel_provider_id)))

    def remove_favorite(self, user_id: str, channel_provider_id: str) -> bool:
        """Elimina un favorito"""
        sql = "DELETE FROM channel_favorites WHERE user_id = %s AND channel_provider_id = %s"
        count = self.execute_command(sql, (user_id, str(channel_provider_id)))
        return count > 0

    # ============================================================
    # HELPERS: Config
    # ============================================================

    def get_config_value(self, key: str) -> Optional[str]:
        """Obtiene valor de config por key"""
        sql = "SELECT value FROM config WHERE key = %s"
        results = self.execute_query(sql, (key,))
        return results[0]["value"] if results else None

    def get_all_config(self) -> Dict[str, str]:
        """Obtiene todos los config como dict"""
        sql = "SELECT key, value FROM config"
        results = self.execute_query(sql)
        return {r["key"]: r["value"] for r in results if r.get("key")}

    # ============================================================
    # HELPERS: Groups / Countries
    # ============================================================

    def get_distinct_groups(
        self, table: str, countries: Optional[List[str]] = None
    ) -> List[str]:
        """
        Obtiene grupos distintos de una tabla.
        """
        if countries and len(countries) > 0:
            placeholders = ",".join(["%s"] * len(countries))
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

        return [row["grupo"] for row in results if row.get("grupo")]

    def get_distinct_countries(
        self, table: str, use_language_names: bool = False
    ) -> List[Dict[str, str]]:
        """
        Obtiene países/idiomas distintos de una tabla usando GROUP BY.
        Si use_language_names=True, mapea códigos a nombres de idioma.
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
            "AD": "Andorra",
            "AE": "Emiratos Árabes Unidos",
            "AF": "Afganistán",
            "AL": "Albania",
            "AM": "Armenia",
            "AR": "Argentina",
            "AT": "Austria",
            "AU": "Australia",
            "AZ": "Azerbaiyán",
            "BE": "Bélgica",
            "BG": "Bulgaria",
            "BH": "Baréin",
            "BR": "Brasil",
            "BY": "Bielorrusia",
            "CA": "Canadá",
            "CG": "República del Congo",
            "CH": "Suiza",
            "CY": "Chipre",
            "CZ": "República Checa",
            "DE": "Alemania",
            "DK": "Dinamarca",
            "ES": "España",
            "FI": "Finlandia",
            "FR": "Francia",
            "GE": "Georgia",
            "GR": "Grecia",
            "HK": "Hong Kong",
            "HR": "Croacia",
            "HU": "Hungría",
            "ID": "Indonesia",
            "IE": "Irlanda",
            "IL": "Israel",
            "IN": "India",
            "IQ": "Irak",
            "IR": "Irán",
            "IS": "Islandia",
            "IT": "Italia",
            "JP": "Japón",
            "KA": "Kazajistán",
            "KO": "Corea del Sur",
            "KU": "Kuwait",
            "LA": "Laos",
            "LT": "Lituania",
            "LU": "Luxemburgo",
            "LV": "Letonia",
            "MK": "Macedonia del Norte",
            "MT": "Malta",
            "MU": "Mauricio",
            "MX": "México",
            "MY": "Malasia",
            "NA": "Namibia",
            "NL": "Países Bajos",
            "NO": "Noruega",
            "NP": "Nepal",
            "NZ": "Nueva Zelanda",
            "PH": "Filipinas",
            "PK": "Pakistán",
            "PL": "Polonia",
            "PT": "Portugal",
            "RO": "Rumania",
            "RS": "Serbia",
            "RU": "Rusia",
            "SE": "Suecia",
            "SG": "Singapur",
            "SI": "Eslovenia",
            "SK": "Eslovaquia",
            "SL": "Sierra Leona",
            "SU": "Sudán",
            "TH": "Tailandia",
            "TR": "Turquía",
            "TW": "Taiwán",
            "UA": "Ucrania",
            "UK": "Reino Unido",
            "US": "Estados Unidos",
            "UZ": "Uzbekistán",
            "VT": "Vaticano",
            "WC": "Islas Cook",
            "ZA": "Sudáfica",
            "AS": "Asia",
            "CR": "Costa Rica",
            "EU": "Unión Europea",
            "EX": "Ex-Yugoslavia",
            "HI": "India",
            "IC": "Islandia",
            "SW": "Suecia",
            "TA": "Taiwán",
            "UR": "Pakistán",
            "VE": "Venezuela",
            "WO": "Mundial",
        }

        language_names = {
            "ES": "Español",
            "EN": "Inglés",
            "LATAM": "Español Latinoamericano",
            "LAT": "Español Latinoamericano",
            "LA": "Español Latinoamericano",
            "LATINO": "Español Latinoamericano",
            "ESP": "Español",
            "ENG": "Inglés",
            "SPANISH": "Español",
            "ENGLISH": "Inglés",
            "VOSE": "VOSE",
            "CAST": "Castellano",
            "CASTELLANO": "Castellano",
            "SUB": "Subtitulado",
            "SUBTITULADO": "Subtitulado",
        }

        names_map = language_names if use_language_names else country_names

        countries = []
        for row in results:
            code = row["country"]
            countries.append({"code": code, "name": names_map.get(code, code)})

        countries.sort(key=lambda c: (0 if c["code"] in ("ES", "US") else 1, c["name"]))
        return countries

    def get_distinct_groups_catalog(self, content_type: str) -> List[str]:
        table = "movies_catalog" if content_type == "movies" else "series_catalog"
        sql = f"""
            SELECT DISTINCT group_normalizado AS grupo
            FROM {table}
            WHERE group_normalizado IS NOT NULL AND group_normalizado != ''
            ORDER BY 1 ASC
        """
        results = self.execute_query(sql)
        return [row["grupo"] for row in results if row.get("grupo")]

    def get_distinct_countries_catalog(self, content_type: str) -> List[Dict[str, str]]:
        table = "movies_catalog" if content_type == "movies" else "series_catalog"
        sql = f"""
            SELECT country
            FROM {table}
            WHERE country IS NOT NULL AND country != ''
            GROUP BY country
            ORDER BY country ASC
        """
        results = self.execute_query(sql)

        country_names = {
            "AD": "Andorra", "AE": "Emiratos Árabes Unidos", "AF": "Afganistán",
            "AL": "Albania", "AM": "Armenia", "AR": "Argentina", "AT": "Austria",
            "AU": "Australia", "AZ": "Azerbaiyán", "BE": "Bélgica", "BG": "Bulgaria",
            "BH": "Baréin", "BR": "Brasil", "BY": "Bielorrusia", "CA": "Canadá",
            "CG": "República del Congo", "CH": "Suiza", "CY": "Chipre",
            "CZ": "República Checa", "DE": "Alemania", "DK": "Dinamarca",
            "DO": "República Dominicana", "DZ": "Argelia", "EC": "Ecuador",
            "EG": "Egipto", "ES": "España", "FI": "Finlandia", "FR": "Francia",
            "GB": "Reino Unido", "GR": "Grecia", "GT": "Guatemala", "HK": "Hong Kong",
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

        countries = []
        for row in results:
            code = row["country"]
            countries.append({"code": code, "name": country_names.get(code, code)})
        return countries

    def get_distinct_series_page(
        self,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        upper_group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Obtiene una página de series únicas desde catálogo normalizado.
        """
        return self.get_distinct_series_groups_catalog(
            page=page, page_size=page_size, group=group,
            upper_group=upper_group, country=country,
            search=search, year=year,
        )

    def get_series_groups_page(
        self,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        upper_group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        year: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Obtiene series agrupadas desde catálogo normalizado.
        """
        return self.get_distinct_series_groups_catalog(
            page=page, page_size=page_size, group=group,
            upper_group=upper_group, country=country,
            search=search, year=year,
        )


# Singleton instance
_postgres_service: Optional[PostgresService] = None


def get_postgres_service() -> PostgresService:
    """Obtiene instancia singleton del servicio PostgreSQL"""
    global _postgres_service
    if _postgres_service is None:
        _postgres_service = PostgresService()
    return _postgres_service
