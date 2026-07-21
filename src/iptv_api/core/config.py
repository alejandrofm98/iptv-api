"""
Configuración centralizada para IPTV
Carga configuración IPTV desde PostgreSQL y variables de entorno locales
"""

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

import iptv_api.core.constants as CONSTANTS


def _load_environment() -> None:
    """Carga variables de entorno desde múltiples ubicaciones"""
    env_paths = [
        Path(__file__).parent / ".env",
        Path(__file__).parent.parent / "docker" / ".env",
        Path(__file__).parent.parent / ".env",
    ]

    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path)
            return


_load_environment()


class Settings:
    """
    Configuración centralizada de la aplicación

    - Variables de entorno locales (.env)
    - Configuración dinámica (tabla config en PostgreSQL)
    """

    def __init__(self):
        # ===== API =====
        self.api_secret_key: str = os.getenv(
            CONSTANTS.API_SECRET_ENV_KEY, CONSTANTS.API_SECRET_DEFAULT
        )

        # ===== JWT Authentication =====
        self.jwt_secret: str = os.getenv(CONSTANTS.JWT_SECRET_ENV_KEY, CONSTANTS.JWT_SECRET_DEFAULT)

        # ===== Servidor =====
        self.session_timeout_minutes: int = CONSTANTS.DEFAULT_SESSION_TIMEOUT_MINUTES
        self.cleanup_interval_minutes: int = CONSTANTS.DEFAULT_CLEANUP_INTERVAL_MINUTES

        # ===== Public Domain =====
        self.public_domain: str = os.getenv(
            CONSTANTS.PUBLIC_DOMAIN_ENV, CONSTANTS.PUBLIC_DOMAIN_DEFAULT_LOCAL
        )

        # ===== PostgreSQL =====
        self.pg_host = os.getenv("PG_HOST", "")
        self.pg_port = int(os.getenv("PG_PORT", "5432"))
        self.pg_database = os.getenv("PG_DATABASE", "postgres")
        self.pg_user = os.getenv("PG_USER", "")
        self.pg_password = os.getenv("PG_PASSWORD", "")

        # ===== IPTV =====
        self.iptv_user: str | None = None
        self.iptv_pass: str | None = None
        self.iptv_base_url: str | None = None
        self.iptv_source_url: str | None = None

        # ===== Estado interno =====
        self._config_loaded: bool = False

        self._load_config()

    def _load_config(self) -> None:
        """Carga configuración dinámica desde PostgreSQL usando psycopg2 directamente."""
        if not self.pg_host:
            return

        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor

            conn_str = f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"
            conn = psycopg2.connect(conn_str, connect_timeout=5)
            try:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT key, value FROM config")
                    config = {r["key"]: r["value"] for r in cur.fetchall() if r.get("key")}
            finally:
                conn.close()

            self.iptv_user = config.get(CONSTANTS.IPTV_USERNAME_KEY)
            self.iptv_pass = config.get(CONSTANTS.IPTV_PASSWORD_KEY)
            self.iptv_base_url = config.get(CONSTANTS.IPTV_BASE_URL_KEY)

            if CONSTANTS.SESSION_TIMEOUT_KEY in config:
                self.session_timeout_minutes = int(config[CONSTANTS.SESSION_TIMEOUT_KEY])

            if CONSTANTS.CLEANUP_INTERVAL_KEY in config:
                self.cleanup_interval_minutes = int(config[CONSTANTS.CLEANUP_INTERVAL_KEY])

            if self.iptv_user and self.iptv_pass and self.iptv_base_url:
                self.iptv_source_url = (
                    f"{self.iptv_base_url}/get.php?"
                    f"username={self.iptv_user}&"
                    f"password={self.iptv_pass}&"
                    f"type={CONSTANTS.IPTV_PLAYLIST_TYPE}&"
                    f"output={CONSTANTS.IPTV_OUTPUT_FORMAT}"
                )
                self._config_loaded = True

        except Exception:
            pass

    def reload_config(self) -> bool:
        """Recarga configuración desde PostgreSQL"""
        self._config_loaded = False
        self._load_config()
        return self._config_loaded

    def is_postgres_configured(self) -> bool:
        return bool(self.pg_host)

    def is_iptv_configured(self) -> bool:
        return bool(self.iptv_user and self.iptv_pass and self.iptv_base_url)

    def is_valid(self) -> bool:
        return self.is_postgres_configured() and self.is_iptv_configured()

    def get_postgres_connection_string(self) -> str:
        """Obtiene string de conexión a PostgreSQL"""
        if not self.pg_host:
            raise ValueError(
                "No hay configuración PostgreSQL. Configura PG_HOST/PG_USER/PG_PASSWORD"
            )
        return f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"

    def validate(self, verbose: bool = True) -> bool:
        errors = []
        warnings_list = []

        # PostgreSQL
        if not self.pg_host:
            errors.append("PG_HOST no configurado")
        if not self.pg_user:
            errors.append("PG_USER no configurado")
        if not self.pg_password:
            warnings_list.append("PG_PASSWORD no configurado (puede estar vacío en local)")

        # IPTV
        if not self.iptv_user:
            errors.append("IPTV_USERNAME no encontrado en tabla config")
        if not self.iptv_pass:
            errors.append("IPTV_PASSWORD no encontrado en tabla config")
        if not self.iptv_base_url:
            warnings_list.append("IPTV_BASE_URL no configurado")

        # API
        if self.api_secret_key == CONSTANTS.API_SECRET_DEFAULT:
            warnings_list.append("API_SECRET_KEY usando valor por defecto (cámbialo en producción)")

        # JWT
        if self.jwt_secret == CONSTANTS.JWT_SECRET_DEFAULT:
            warnings_list.append("JWT_SECRET usando valor por defecto (cámbialo en producción)")

        if verbose:
            if errors:
                print("\n❌ Errores de configuración:")
                for e in errors:
                    print(f"   - {e}")

            if warnings_list:
                print("\n⚠️  Advertencias:")
                for w in warnings_list:
                    print(f"   - {w}")

            if not errors and not warnings_list:
                print("\n✅ Configuración válida")

        return not errors

    def __repr__(self) -> str:
        is_docker = (
            os.path.exists(CONSTANTS.DOCKER_ENV_PATH)
            or os.getenv(CONSTANTS.DOCKER_ENV_FLAG) == CONSTANTS.DOCKER_ENV_VALUE
        )

        mode = "🐳 Docker" if is_docker else "💻 Local"

        return (
            f"Settings(\n"
            f"  Modo: {mode}\n"
            f"  PostgreSQL: {'✓' if self.is_postgres_configured() else '✗'}\n"
            f"  IPTV User: {self.iptv_user or '✗'}\n"
            f"  IPTV Config: {'✓' if self.is_iptv_configured() else '✗'}\n"
            f"  Session Timeout: {self.session_timeout_minutes}min\n"
            f"  Cleanup Interval: {self.cleanup_interval_minutes}min\n"
            f")"
        )


@lru_cache
def get_settings() -> Settings:
    """Obtiene configuración cacheada (singleton)"""
    return Settings()
