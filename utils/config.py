"""
Configuración centralizada para IPTV
Carga configuración IPTV desde Supabase y variables de entorno locales
"""

import os
import warnings
from pathlib import Path
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from supabase import create_client, Client

import utils.constants as CONSTANTS

# Suprimir warnings de storage endpoint
warnings.filterwarnings(
  'ignore',
  message='.*Storage endpoint URL.*trailing slash.*'
)


def _load_environment() -> None:
  """Carga variables de entorno desde múltiples ubicaciones"""
  env_paths = [
    Path(__file__).parent / '.env',
    Path(__file__).parent.parent / 'docker' / '.env',
    Path(__file__).parent.parent / '.env',
  ]

  for env_path in env_paths:
    if env_path.exists():
      load_dotenv(env_path)
      return


# Cargar .env al importar el módulo
_load_environment()


class Settings:
  """
  Configuración centralizada de la aplicación

  - Variables de entorno locales (.env)
  - Configuración dinámica (tabla config en Supabase)
  """

  # ===== Supabase =====
  supabase_url: str = os.getenv(CONSTANTS.SUPABASE_ENV_URL)
  supabase_key: str = os.getenv(CONSTANTS.SUPABASE_ENV_KEY)

  # ===== PostgreSQL (para consultas SQL directas) =====
  pg_host: str = os.getenv('PG_HOST', '')
  pg_port: int = int(os.getenv('PG_PORT', '5432'))
  pg_database: str = os.getenv('PG_DATABASE', 'postgres')
  pg_user: str = os.getenv('PG_USER', '')
  pg_password: str = os.getenv('PG_PASSWORD', '')

  # ===== API =====
  api_secret_key: str = os.getenv(CONSTANTS.API_SECRET_ENV_KEY)

  # ===== JWT Authentication =====
  jwt_secret: str = os.getenv(CONSTANTS.JWT_SECRET_ENV_KEY, CONSTANTS.JWT_SECRET_DEFAULT)

  # ===== IPTV =====
  iptv_user: Optional[str] = None
  iptv_pass: Optional[str] = None
  iptv_base_url: Optional[str] = None
  iptv_source_url: Optional[str] = None

  # ===== Servidor =====
  session_timeout_minutes: int = CONSTANTS.DEFAULT_SESSION_TIMEOUT_MINUTES
  cleanup_interval_minutes: int = CONSTANTS.DEFAULT_CLEANUP_INTERVAL_MINUTES

  # ===== Public Domain =====
  public_domain: str = os.getenv(CONSTANTS.PUBLIC_DOMAIN_ENV, CONSTANTS.PUBLIC_DOMAIN_DEFAULT_LOCAL)

  # ===== Estado interno =====
  _config_loaded: bool = False
  _client_cache: Optional[Client] = None

  def __init__(self):
    self._ensure_supabase_url()
    self._load_config()

  def _ensure_supabase_url(self) -> None:
    """Asegura que la URL de Supabase termine con /"""
    if self.supabase_url and not self.supabase_url.endswith('/'):
      self.supabase_url += '/'

  def _load_config(self) -> None:
    """Carga configuración dinámica desde Supabase"""
    if not self.is_supabase_configured():
      return

    try:
      client = self.get_supabase_client()
      response = (
        client
        .table(CONSTANTS.SUPABASE_CONFIG_TABLE)
        .select('key, value')
        .execute()
      )

      if not response.data:
        return

      config = {item['key']: item['value'] for item in response.data}

      # ===== IPTV =====
      self.iptv_user = config.get(CONSTANTS.IPTV_USERNAME_KEY)
      self.iptv_pass = config.get(CONSTANTS.IPTV_PASSWORD_KEY)
      self.iptv_base_url = config.get(CONSTANTS.IPTV_BASE_URL_KEY)

      # ===== Servidor =====
      if CONSTANTS.SESSION_TIMEOUT_KEY in config:
        self.session_timeout_minutes = int(config[CONSTANTS.SESSION_TIMEOUT_KEY])

      if CONSTANTS.CLEANUP_INTERVAL_KEY in config:
        self.cleanup_interval_minutes = int(config[CONSTANTS.CLEANUP_INTERVAL_KEY])

      # ===== Playlist =====
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
      # Silencioso; usar validate() para diagnóstico
      pass

  def reload_config(self) -> bool:
    """Recarga configuración desde Supabase"""
    self._config_loaded = False
    self._load_config()
    return self._config_loaded

  def is_supabase_configured(self) -> bool:
    return bool(self.supabase_url and self.supabase_key)

  def is_iptv_configured(self) -> bool:
    return bool(self.iptv_user and self.iptv_pass and self.iptv_base_url)

  def is_valid(self) -> bool:
    return self.is_supabase_configured() and self.is_iptv_configured()

  def get_supabase_client(self) -> Client:
    """Obtiene cliente de Supabase (cacheado)"""
    if self._client_cache:
      return self._client_cache

    if not self.is_supabase_configured():
      raise ValueError(
        "Supabase no está configurado. Revisa SUPABASE_URL y SUPABASE_KEY"
      )

    try:
      base_url = self.supabase_url.rstrip('/')
      storage_url = f"{base_url}/storage/v1/"

      try:
        from supabase import ClientOptions
        options = ClientOptions(storage={"url": storage_url})
        client = create_client(self.supabase_url, self.supabase_key, options)
      except (ImportError, TypeError):
        client = create_client(self.supabase_url, self.supabase_key)

      self._client_cache = client
      return client

    except Exception as e:
      raise RuntimeError(f"Error al crear cliente Supabase: {e}")

  def get_postgres_connection_string(self) -> str:
    """Obtiene string de conexión a PostgreSQL"""
    # Si no hay configuración explícita, extraer de Supabase URL
    if not self.pg_host and self.supabase_url:
      # Extraer host de la URL de Supabase
      # Ej: https://xyz.supabase.co -> xyz.supabase.co
      from urllib.parse import urlparse
      parsed = urlparse(self.supabase_url)
      host = parsed.netloc.replace('.co', '.co:5432')
      return f"postgresql://postgres:{self.supabase_key}@{host}/postgres"
    
    # Usar configuración explícita
    return f"postgresql://{self.pg_user}:{self.pg_password}@{self.pg_host}:{self.pg_port}/{self.pg_database}"

  def validate(self, verbose: bool = True) -> bool:
    errors = []
    warnings_list = []

    # Supabase
    if not self.supabase_url:
      errors.append("SUPABASE_URL no configurada")
    if not self.supabase_key:
      errors.append("SUPABASE_KEY no configurada")

    # IPTV
    if not self.iptv_user:
      errors.append("IPTV_USERNAME no encontrado en tabla config")
    if not self.iptv_pass:
      errors.append("IPTV_PASSWORD no encontrado en tabla config")
    if not self.iptv_base_url:
      warnings_list.append("IPTV_BASE_URL no configurado")

    # API
    if self.api_secret_key == CONSTANTS.API_SECRET_DEFAULT:
      warnings_list.append(
        "API_SECRET_KEY usando valor por defecto (cámbialo en producción)"
      )

    # JWT
    # <--- NUEVO: Validación de seguridad para JWT
    if self.jwt_secret == CONSTANTS.JWT_SECRET_DEFAULT:
      warnings_list.append(
        "JWT_SECRET usando valor por defecto (cámbialo en producción)"
      )

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
      os.path.exists(CONSTANTS.DOCKER_ENV_PATH) or
      os.getenv(CONSTANTS.DOCKER_ENV_FLAG) == CONSTANTS.DOCKER_ENV_VALUE
    )

    mode = "🐳 Docker" if is_docker else "💻 Local"

    return (
      f"Settings(\n"
      f"  Modo: {mode}\n"
      f"  Supabase: {'✓' if self.is_supabase_configured() else '✗'}\n"
      f"  IPTV User: {self.iptv_user or '✗'}\n"
      f"  IPTV Config: {'✓' if self.is_iptv_configured() else '✗'}\n"
      f"  Session Timeout: {self.session_timeout_minutes}min\n"
      f"  Cleanup Interval: {self.cleanup_interval_minutes}min\n"
      f")"
    )


@lru_cache()
def get_settings() -> Settings:
  """Obtiene configuración cacheada (singleton)"""
  return Settings()