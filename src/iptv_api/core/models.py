"""
Modelos Pydantic para IPTV API
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

# ============================================
# Enums
# ============================================


class DeviceType(str, Enum):
    MOBILE = "mobile"
    TV = "tv"
    DESKTOP = "desktop"
    IPTV_APP = "iptv_app"
    UNKNOWN = "unknown"


# ============================================
# User Models
# ============================================


class UserCreate(BaseModel):
    """Modelo para crear usuario"""

    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6)
    max_connections: int = Field(default=5, ge=1, le=10)
    role: str = Field(default="user")  # <--- NUEVO
    expires_at: datetime | None = None


class UserUpdate(BaseModel):
    """Modelo para actualizar usuario"""

    password: str | None = Field(None, min_length=6)
    max_connections: int | None = Field(None, ge=1, le=10)
    is_active: bool | None = None
    expires_at: datetime | None = None
    role: str | None = None  # <--- NUEVO


class UserResponse(BaseModel):
    """Respuesta de usuario"""

    id: str
    username: str
    max_connections: int
    is_active: bool
    expires_at: datetime | None
    created_at: datetime
    active_devices: int = 0
    role: str  # <--- NUEVO

    model_config = ConfigDict(from_attributes=True)


class UserWithDevices(UserResponse):
    """Usuario con lista de dispositivos"""

    devices: list["DeviceResponse"] = Field(default_factory=list)


# ============================================
# Device/Session Models
# ============================================


class DeviceResponse(BaseModel):
    """Respuesta de dispositivo"""

    id: str
    device_id: str
    device_name: str | None
    device_type: DeviceType
    ip_address: str | None
    last_activity: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionInfo(BaseModel):
    """Información de sesión activa"""

    user_id: str
    username: str
    device_id: str
    device_name: str
    device_type: DeviceType
    ip_address: str
    connected_since: datetime
    last_activity: datetime


# ============================================
# Auth Models
# ============================================


class ValidateCredentials(BaseModel):
    """Modelo para validar credenciales"""

    username: str
    password: str


class Token(BaseModel):  # <--- NUEVO MODELO
    """Modelo de respuesta para Token JWT"""

    access_token: str
    token_type: str
    role: str


class AuthResult(BaseModel):
    """Resultado de autenticación"""

    valid: bool
    user_id: str | None = None
    username: str | None = None
    message: str
    can_connect: bool = False
    current_devices: int = 0
    max_devices: int = 0


# ============================================
# Pagination Models
# ============================================


class PaginationParams(BaseModel):
    """Parámetros de paginación estándar"""

    page: int = Field(1, ge=1, description="Número de página")
    page_size: int = Field(50, ge=1, le=100, description="Items por página")


class PaginatedResponse(BaseModel):
    """Respuesta paginada estándar"""

    items: list[dict]
    total: int
    page: int
    page_size: int
    pages: int
    has_next: bool
    has_prev: bool


# ============================================
# Stats Models
# ============================================


class SystemStats(BaseModel):
    """Estadísticas del sistema"""

    total_users: int
    active_users: int
    total_sessions: int
    total_channels: int
    total_movies: int
    total_series: int


class UserStats(BaseModel):
    """Estadísticas de usuario"""

    user_id: str
    username: str
    active_devices: int
    max_connections: int
    total_streams_today: int
    is_active: bool
    expires_at: datetime | None


# Actualizar forward references
UserWithDevices.model_rebuild()


# ============================================
# Calendar Models
# ============================================


class ChannelResolved(BaseModel):
    """Canal resuelto con su información"""

    channel_id: str
    display_name: str
    quality: str
    priority: int
    source_name: str
    logo: str | None = None
    stream_url: str | None = None
    content_type: str | None = None
    provider_id: str | None = None


class CalendarEvent(BaseModel):
    """Evento del calendario con canales resueltos"""

    id: str
    fecha: str | None
    hora: str | None
    competicion: str | None
    subtitulo_competicion: str | None = None
    categoria: str | None
    equipos: str | None
    imagen_evento: str | None = None
    canales_original: list[str]
    canales_resueltos: list[ChannelResolved]


class CalendarDayResponse(BaseModel):
    """Respuesta de eventos por día"""

    fecha: str
    total_eventos: int
    eventos: list[CalendarEvent]


# ============================================
# Replay Models
# ============================================


class ReplaySource(BaseModel):
    """Fuente individual disponible para un replay"""

    label: str
    token: str | None = None
    token_enc: str | None = None
    source_index: int | None = None
    button_index: int | None = None
    embed_url: str | None = None
    web_embed_url: str | None = None
    provider: str | None = None
    provider_url: str | None = None
    provider_access_id: str | None = None
    provider_video_id: str | None = None
    provider_playlist_id: str | None = None
    stream_url: str | None = None
    stream_format: str | None = None
    stream_resolved_at: datetime | None = None


class ReplaySourceGroup(BaseModel):
    """Grupo de fuentes de un replay"""

    group: str
    sources: list[ReplaySource]


class ReplayItem(BaseModel):
    """Replay UFC normalizado para la web"""

    slug: str
    source_site: str
    title: str
    event_name: str | None = None
    event_type: str | None = None
    event_date: str | None = None
    post_url: str
    featured_image_url: str | None = None
    description: str | None = None
    video_sources: list[ReplaySourceGroup] = []
    match_card: list[str] = []


class ReplayStats(BaseModel):
    """Estadisticas de replays"""

    total_replays: int


# ============================================
# Watch Progress Models
# ============================================


class WatchProgressUpsert(BaseModel):
    """Modelo para crear/actualizar progreso de visualización"""

    content_type: str = Field(..., pattern="^(movie|series)$")
    position_ms: int = Field(..., ge=0)
    duration_ms: int = Field(..., ge=0)
    series_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    title: str = Field("", max_length=255)
    image_url: str = ""


class WatchProgressResponse(BaseModel):
    """Respuesta de progreso de visualización"""

    content_id: str
    content_type: str
    position_ms: int
    duration_ms: int
    series_name: str | None = None
    season_number: int | None = None
    episode_number: int | None = None
    title: str
    image_url: str
    last_watched_at: str


class ChannelFavoriteCreate(BaseModel):
    """Modelo para agregar un canal a favoritos."""

    channel_provider_id: str = Field(..., min_length=1, max_length=100)


class ChannelFavoriteResponse(BaseModel):
    """Respuesta de favorito de canal."""

    user_id: str
    channel_provider_id: str
    provider_id: str
    created_at: str | None = None
