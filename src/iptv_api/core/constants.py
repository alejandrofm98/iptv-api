"""
Constantes globales de configuración para IPTV
"""

# ===== Config =====
CONFIG_TABLE = "config"

# ===== API =====
API_SECRET_ENV_KEY = "API_SECRET_KEY"
API_SECRET_DEFAULT = "your-secret-key-change-in-production"

# ===== JWT Authentication =====
JWT_SECRET_ENV_KEY = "JWT_SECRET"
JWT_SECRET_DEFAULT = "cambia-esto-por-una-clave-secreta-muy-larga"
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 horas

# ===== Servidor (keys en tabla config) =====
SESSION_TIMEOUT_KEY = "SESSION_TIMEOUT_MINUTES"
CLEANUP_INTERVAL_KEY = "CLEANUP_INTERVAL_MINUTES"

# ===== Defaults =====
DEFAULT_SESSION_TIMEOUT_MINUTES = 30
DEFAULT_CLEANUP_INTERVAL_MINUTES = 5

# ===== Docker =====
DOCKER_ENV_FLAG = "DOCKER_CONTAINER"
DOCKER_ENV_VALUE = "true"
DOCKER_ENV_PATH = "/.dockerenv"


SUPABASE_DEFAULT_BATCH_SIZE = 5000
SUPABASE_DEFAULT_MAX_WORKERS = 1
SUPABASE_DEFAULT_MAX_RETRIES = 3

# ===== Tablas de Base de Datos =====
CHANNELS_TABLE = "channels"
MOVIES_TABLE = "movies"
SERIES_TABLE = "series"
SYNC_METADATA_TABLE = "sync_metadata"

PUBLIC_DOMAIN_ENV = "PUBLIC_DOMAIN"
PUBLIC_DOMAIN_DEFAULT_LOCAL = "http://localhost:8000"
PUBLIC_DOMAIN_DEFAULT_DOCKER = "https://tudominio.com"

# ===== Logos y recursos =====
DEFAULT_LOGO_URL = "https://via.placeholder.com/150"

# ===== Tipos de contenido IPTV =====
CONTENT_TYPE_CHANNEL = "channel"
CONTENT_TYPE_MOVIE = "movie"
CONTENT_TYPE_SERIE = "serie"

# ===== Patrones regex =====
SERIES_PATTERN = r"[Ss](\d{1,2})\s*[Ee](\d{1,2})"
COUNTRY_CODE_PATTERN = r"^[|\s]*([A-Z]{2})[|\s]"

# ===== URLs y paths =====
URL_SERIES_PATH = "/series/"
URL_MOVIE_PATH = "/movie/"

# ===== Configuración de inserción masiva =====
DELETE_BATCH_LIMIT = 5000
MAX_DELETE_ATTEMPTS = 100

# ===== Timeouts y tiempos =====
PLAYLIST_DOWNLOAD_TIMEOUT = 300  # 5 minutos
DELETE_BATCH_SLEEP = 0.1  # segundos

# ===== Metadata sync =====
SYNC_METADATA_ID = "iptv_sync"

# ===== M3U Parsing =====
M3U_EXTINF_PREFIX = "#EXTINF:"
M3U_GROUP_TITLE_ATTR = 'group-title="'
M3U_TVG_LOGO_ATTR = 'tvg-logo="'
M3U_TVG_ID_ATTR = 'tvg-id="'

# ===== HTTP Headers =====
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
