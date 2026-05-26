import uuid
from datetime import datetime

from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, PrimaryKeyConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Channel(Base):
    __tablename__ = "channels"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id = Column(String(50), nullable=True)
    nombre = Column(String, nullable=True)
    nombre_normalizado = Column(String, nullable=True)
    logo = Column(String, nullable=True)
    grupo = Column(String, nullable=True)
    grupo_normalizado = Column(String, nullable=True)
    country = Column(String(10), nullable=True)
    url = Column(String, nullable=True)
    numero = Column(Integer, nullable=True)
    tvg_id = Column(String, nullable=True)
    tvg_name = Column(String, nullable=True)
    tvg_logo = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class ChannelFavorite(Base):
    __tablename__ = "channel_favorites"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel_provider_id = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        PrimaryKeyConstraint("user_id", "channel_provider_id"),
    )
