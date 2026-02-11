"""
Servicio de conexión a PostgreSQL usando psycopg2
Para consultas SQL directas y complejas
"""
import os
from typing import List, Dict, Any, Optional
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2 import pool
from contextlib import contextmanager

from utils.config import get_settings


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
                1, 5,
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

        raise ValueError("No se pudo construir string de conexión a PostgreSQL. "
                        "Configura PG_HOST/PG_USER/PG_PASSWORD o SUPABASE_URL/SUPABASE_KEY")

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
        Ejecuta una consulta SELECT y retorna los resultados
        
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
        Ejecuta un comando SQL (INSERT, UPDATE, DELETE)
        
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
        Obtiene grupos distintos de una tabla
        
        Args:
            table: Nombre de la tabla (channels, movies, series)
            countries: Lista opcional de códigos de país para filtrar
            
        Returns:
            Lista de grupos ordenados alfabéticamente
        """
        if countries and len(countries) > 0:
            # Usar parámetros para prevenir SQL injection
            placeholders = ','.join(['%s'] * len(countries))
            sql = f"""
                SELECT DISTINCT grupo 
                FROM {table} 
                WHERE grupo IS NOT NULL 
                AND grupo != '' 
                AND country IN ({placeholders})
                ORDER BY grupo ASC
            """
            results = self.execute_query(sql, tuple(countries))
        else:
            sql = f"""
                SELECT DISTINCT grupo 
                FROM {table} 
                WHERE grupo IS NOT NULL 
                AND grupo != ''
                ORDER BY grupo ASC
            """
            results = self.execute_query(sql)
        
        return [row['grupo'] for row in results]
    
    def get_distinct_countries(self, table: str) -> List[Dict[str, str]]:
        """
        Obtiene países distintos de una tabla usando GROUP BY
        
        Args:
            table: Nombre de la tabla
            
        Returns:
            Lista de diccionarios con 'code' y 'name'
        """
        sql = f"""
            SELECT country
            FROM {table}
            WHERE country IS NOT NULL AND country != ''
            GROUP BY country
            ORDER BY country ASC
        """
        results = self.execute_query(sql)
        
        # Mapeo completo de códigos ISO a nombres de países en español
        country_names = {
            'AD': 'Andorra',
            'AE': 'Emiratos Árabes Unidos',
            'AF': 'Afganistán',
            'AL': 'Albania',
            'AM': 'Armenia',
            'AR': 'Argentina',
            'AT': 'Austria',
            'AU': 'Australia',
            'AZ': 'Azerbaiyán',
            'BE': 'Bélgica',
            'BG': 'Bulgaria',
            'BH': 'Baréin',
            'BR': 'Brasil',
            'BY': 'Bielorrusia',
            'CA': 'Canadá',
            'CG': 'República del Congo',
            'CH': 'Suiza',
            'CY': 'Chipre',
            'CZ': 'República Checa',
            'DE': 'Alemania',
            'DK': 'Dinamarca',
            'ES': 'España',
            'FI': 'Finlandia',
            'FR': 'Francia',
            'GE': 'Georgia',
            'GR': 'Grecia',
            'HK': 'Hong Kong',
            'HR': 'Croacia',
            'HU': 'Hungría',
            'ID': 'Indonesia',
            'IE': 'Irlanda',
            'IL': 'Israel',
            'IN': 'India',
            'IQ': 'Irak',
            'IR': 'Irán',
            'IS': 'Islandia',
            'IT': 'Italia',
            'JP': 'Japón',
            'KA': 'Kazajistán',
            'KO': 'Corea del Sur',
            'KU': 'Kuwait',
            'LA': 'Laos',
            'LT': 'Lituania',
            'LU': 'Luxemburgo',
            'LV': 'Letonia',
            'MK': 'Macedonia del Norte',
            'MT': 'Malta',
            'MU': 'Mauricio',
            'MX': 'México',
            'MY': 'Malasia',
            'NA': 'Namibia',
            'NL': 'Países Bajos',
            'NO': 'Noruega',
            'NP': 'Nepal',
            'NZ': 'Nueva Zelanda',
            'PH': 'Filipinas',
            'PK': 'Pakistán',
            'PL': 'Polonia',
            'PT': 'Portugal',
            'RO': 'Rumania',
            'RS': 'Serbia',
            'RU': 'Rusia',
            'SE': 'Suecia',
            'SG': 'Singapur',
            'SI': 'Eslovenia',
            'SK': 'Eslovaquia',
            'SL': 'Sierra Leona',
            'SU': 'Sudán',
            'TH': 'Tailandia',
            'TR': 'Turquía',
            'TW': 'Taiwán',
            'UA': 'Ucrania',
            'UK': 'Reino Unido',
            'US': 'Estados Unidos',
            'UZ': 'Uzbekistán',
            'VT': 'Vaticano',
            'WC': 'Islas Cook',
            'ZA': 'Sudáfrica'
        }
        
        countries = []
        for row in results:
            code = row['country']
            countries.append({
                'code': code,
                'name': country_names.get(code, code)
            })
        
        # Ordenar alfabéticamente por nombre
        countries.sort(key=lambda c: c['name'])
        
        return countries


# Singleton instance
_postgres_service: Optional[PostgresService] = None


def get_postgres_service() -> PostgresService:
    """Obtiene instancia singleton del servicio PostgreSQL"""
    global _postgres_service
    if _postgres_service is None:
        _postgres_service = PostgresService()
    return _postgres_service
