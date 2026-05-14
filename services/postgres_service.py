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
        """Obtiene película con metadata TMDB por id interno o provider_id"""
        sql = """
            SELECT m.*,
                mm.overview_es,
                mm.overview_en,
                mm.vote_average,
                mm.vote_count,
                mm.genres,
                mm.backdrop_path,
                mm.poster_path as tmdb_poster_path,
                mm.runtime_minutes,
                mm.tagline,
                mm.tmdb_id,
                mm.title as tmdb_title,
                mm.release_date,
                mm.popularity,
                mm.status
            FROM movies m
            LEFT JOIN movies_metadata mm ON m.provider_id = mm.provider_id
            WHERE m.id = %s OR m.provider_id = %s
            LIMIT 1
        """
        results = self.execute_query(sql, (movie_id, movie_id))
        return results[0] if results else None

    def get_series_with_metadata(self, series_id: str) -> Optional[Dict[str, Any]]:
        """Obtiene serie con metadata TMDB por id interno o series_key"""
        sql = """
            SELECT s.*,
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
                seasons.total_seasons
            FROM series s
            LEFT JOIN series_metadata sm ON s.series_key = sm.series_key
            LEFT JOIN (
                SELECT series_key, COUNT(DISTINCT temporada) AS total_seasons
                FROM series
                WHERE temporada IS NOT NULL
                GROUP BY series_key
            ) seasons ON s.series_key = seasons.series_key
            WHERE s.id = %s OR s.series_key = %s OR s.provider_id = %s
            LIMIT 1
        """
        results = self.execute_query(sql, (series_id, series_id, series_id))
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

    def get_distinct_movies_page(
        self,
        page: int,
        page_size: int,
        group: Optional[str] = None,
        upper_group: Optional[str] = None,
        country: Optional[str] = None,
        search: Optional[str] = None,
        year: Optional[int] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Obtiene películas deduplicadas por nombre_dedup_key con paginación usando ROW_NUMBER()"""
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

        dedup_key_expr = """
            COALESCE(
                NULLIF(tmdb_id::text, ''),
                NULLIF(nombre_dedup_key, ''),
                LOWER(NULLIF(nombre_normalizado, '')),
                LOWER(nombre)
            )
        """

        count_sql = f"""
            SELECT COUNT(DISTINCT {dedup_key_expr}) as total
            FROM movies
            {where_clause}
        """
        count_result = self.execute_query(count_sql, tuple(params))
        total = count_result[0]["total"] if count_result else 0

        # Usar ROW_NUMBER() para paginar correctamente después de la deduplicación
        # El orden final debe ser consistente: dedup primero, luego номер
        start_row = (page - 1) * page_size + 1
        end_row = page * page_size

        data_sql = f"""
            WITH base AS (
            SELECT *,
            {dedup_key_expr} AS dedup_key
            FROM movies
            {where_clause}
        ),
        deduped AS (
            SELECT DISTINCT ON (dedup_key) *
            FROM base
            ORDER BY dedup_key ASC, year DESC NULLS LAST, numero ASC
        ),
        with_metadata AS (
            SELECT
                d.*,
                mm.overview_es,
                mm.overview_en,
                mm.vote_average,
                mm.vote_count,
                mm.genres,
                mm.backdrop_path,
                mm.poster_path as tmdb_poster_path,
                mm.runtime_minutes,
                mm.tagline,
                mm.tmdb_id,
                mm.title as tmdb_title,
                mm.release_date,
                mm.popularity,
                mm.status
            FROM deduped d
            LEFT JOIN movies_metadata mm
                ON d.provider_id = mm.provider_id
        ),
        numbered AS (
            SELECT *,
            ROW_NUMBER() OVER (ORDER BY year DESC NULLS LAST, nombre_normalizado ASC, id ASC) as rn
            FROM with_metadata
        )
        SELECT * FROM numbered
        WHERE rn BETWEEN %s AND %s
        ORDER BY rn
        """
        data_params = tuple([*params, start_row, end_row])
        items = self.execute_query(data_sql, data_params)

        # Limpiar el campo 'rn' de los resultados
        for item in items:
            item.pop("rn", None)

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
        """Obtiene conteo de todas las tablas de contenido"""
        return {
            "channels": self.count_table("channels"),
            "movies": self.count_table("movies"),
            "series": self.count_table("series"),
            "replays": self.count_table("replays"),
        }

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

    def get_episodes_by_serie_name(self, serie_name: str) -> List[Dict[str, Any]]:
        """Obtiene episodios de una serie ordenados"""
        sql = """
            SELECT * FROM series
            WHERE serie_name = %s
            ORDER BY temporada ASC, episodio ASC
        """
        return self.execute_query(sql, (serie_name,))

    def get_episodes_paginated(
        self, serie_name: str, page: int, page_size: int
    ) -> Tuple[List[Dict[str, Any]], int, List]:
        """Obtiene episodios paginados de una serie"""
        count_sql = "SELECT COUNT(*) as total FROM series WHERE serie_name = %s"
        count_result = self.execute_query(count_sql, (serie_name,))
        total = count_result[0]["total"] if count_result else 0

        offset = (page - 1) * page_size
        data_sql = """
            SELECT * FROM series
            WHERE serie_name = %s
            ORDER BY temporada ASC, episodio ASC
            LIMIT %s OFFSET %s
        """
        items = self.execute_query(data_sql, (serie_name, page_size, offset))

        seasons_sql = "SELECT DISTINCT temporada FROM series WHERE serie_name = %s AND temporada IS NOT NULL ORDER BY temporada"
        seasons_result = self.execute_query(seasons_sql, (serie_name,))
        seasons = [r["temporada"] for r in seasons_result]

        return items, total, seasons

    def get_series_seasons(self, serie_name: str) -> List:
        """Obtiene lista de temporadas distintas de una serie"""
        sql = "SELECT DISTINCT temporada FROM series WHERE serie_name = %s AND temporada IS NOT NULL ORDER BY temporada"
        results = self.execute_query(sql, (serie_name,))
        return [r["temporada"] for r in results]

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
        Obtiene una página de series únicas.
        """
        filters: List[str] = []
        params: List[Any] = []

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
            filters.append(
                "(serie_name ILIKE %s OR nombre_normalizado ILIKE %s OR nombre ILIKE %s)"
            )
            params.extend([f"%{search}%", f"%{search}%", f"%{search}%"])

        if year:
            filters.append("year = %s")
            params.append(year)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        offset = (page - 1) * page_size

        series_key_expr = """
            COALESCE(
                NULLIF(series_key, ''),
                NULLIF(nombre_dedup_key, ''),
                NULLIF(serie_name, ''),
                LOWER(NULLIF(nombre_normalizado, '')),
                LOWER(nombre)
            )
        """

        sql = f"""
        WITH base AS (
            SELECT *,
            {series_key_expr} AS catalog_series_key
            FROM series
            {where_clause}
        ),
        deduped AS (
            SELECT DISTINCT ON (catalog_series_key) *
            FROM base
            ORDER BY catalog_series_key ASC, year DESC, numero ASC
        ),
        season_counts AS (
            SELECT catalog_series_key, COUNT(DISTINCT temporada) AS total_seasons
            FROM base
            WHERE temporada IS NOT NULL
            GROUP BY catalog_series_key
        ),
        with_metadata AS (
            SELECT
                d.*,
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
                sc.total_seasons
            FROM deduped d
            LEFT JOIN series_metadata sm
                ON d.catalog_series_key = sm.series_key
            LEFT JOIN season_counts sc
                ON d.catalog_series_key = sc.catalog_series_key
        ),
        counted AS (
            SELECT *, COUNT(*) OVER() AS _total
            FROM with_metadata
        )
        SELECT *
        FROM counted
        ORDER BY year DESC NULLS LAST
        LIMIT %s OFFSET %s
        """

        all_params = tuple([*params, page_size, offset])
        rows = self.execute_query(sql, all_params)

        total = int(rows[0]["_total"]) if rows else 0

        clean_rows = []
        for row in rows:
            r = dict(row)
            r.pop("_total", None)
            r.pop("catalog_series_key", None)
            clean_rows.append(r)

        return {
            "items": clean_rows,
            "total": total,
        }

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
        Obtiene series agrupadas con total de episodios por serie.
        """
        filters: List[str] = []
        params: List[Any] = []

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
            filters.append("(serie_name ILIKE %s OR nombre_normalizado ILIKE %s)")
            params.extend([f"%{search}%", f"%{search}%"])

        if year:
            filters.append("year = %s")
            params.append(year)

        where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
        offset = (page - 1) * page_size

        series_key_expr = """
            COALESCE(
                NULLIF(series_key, ''),
                NULLIF(nombre_dedup_key, ''),
                NULLIF(serie_name, ''),
                LOWER(NULLIF(nombre_normalizado, '')),
                LOWER(nombre)
            )
        """

        sql = f"""
        WITH base AS (
            SELECT *,
                {series_key_expr} AS catalog_series_key
            FROM series
            {where_clause}
        ),
        grouped AS (
            SELECT
                serie_name,
                MIN(catalog_series_key) AS catalog_series_key,
                COUNT(*) AS total_episodes,
                MAX(year) AS year,
                MAX(logo) AS logo,
                MAX(grupo) AS grupo,
                MAX(grupo_normalizado) AS grupo_normalizado,
                MAX(country) AS country,
                COUNT(DISTINCT temporada) AS total_seasons,
                MIN(numero) AS first_numero,
                MIN(provider_id) AS first_provider_id,
                MIN(id) AS first_id,
                MIN(nombre) AS first_nombre,
                MIN(nombre_normalizado) AS first_nombre_normalizado
            FROM base
            GROUP BY serie_name
            HAVING serie_name IS NOT NULL AND serie_name != ''
        ),
        with_metadata AS (
            SELECT
                g.*,
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
                sm.status
            FROM grouped g
            LEFT JOIN series_metadata sm
                ON g.catalog_series_key = sm.series_key
        ),
        counted AS (
            SELECT *, COUNT(*) OVER() AS _total
            FROM with_metadata
        )
        SELECT *
        FROM counted
        ORDER BY total_episodes DESC NULLS LAST, year DESC NULLS LAST, serie_name ASC
        LIMIT %s OFFSET %s
        """

        all_params = tuple([*params, page_size, offset])
        rows = self.execute_query(sql, all_params)

        total = int(rows[0]["_total"]) if rows else 0

        clean_rows = []
        for row in rows:
            r = dict(row)
            r.pop("_total", None)
            r.pop("catalog_series_key", None)
            provider_id = r.pop("first_provider_id", None)
            r["provider_id"] = provider_id
            r["id"] = r.pop("first_id", None)
            r["nombre"] = r.pop("first_nombre", None)
            r["nombre_normalizado"] = r.pop("first_nombre_normalizado", None)
            clean_rows.append(r)

        return {
            "items": clean_rows,
            "total": total,
        }


# Singleton instance
_postgres_service: Optional[PostgresService] = None


def get_postgres_service() -> PostgresService:
    """Obtiene instancia singleton del servicio PostgreSQL"""
    global _postgres_service
    if _postgres_service is None:
        _postgres_service = PostgresService()
    return _postgres_service
