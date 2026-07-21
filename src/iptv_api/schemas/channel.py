from datetime import datetime

from pydantic import BaseModel


class ChannelItem(BaseModel):
    id: str
    provider_id: str | None = None
    nombre: str | None = None
    nombre_normalizado: str | None = None
    logo: str | None = None
    grupo: str | None = None
    grupo_normalizado: str | None = None
    country: str | None = None
    url: str | None = None
    numero: int | None = None
    tvg_id: str | None = None
    tvg_name: str | None = None
    tvg_logo: str | None = None

    model_config = {"from_attributes": True}


class ChannelFavoriteResponse(BaseModel):
    user_id: str
    channel_provider_id: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
