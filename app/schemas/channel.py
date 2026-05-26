from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ChannelItem(BaseModel):
    id: str
    provider_id: Optional[str] = None
    nombre: Optional[str] = None
    nombre_normalizado: Optional[str] = None
    logo: Optional[str] = None
    grupo: Optional[str] = None
    grupo_normalizado: Optional[str] = None
    country: Optional[str] = None
    url: Optional[str] = None
    numero: Optional[int] = None
    tvg_id: Optional[str] = None
    tvg_name: Optional[str] = None
    tvg_logo: Optional[str] = None

    model_config = {"from_attributes": True}


class ChannelFavoriteResponse(BaseModel):
    user_id: str
    channel_provider_id: str
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}
