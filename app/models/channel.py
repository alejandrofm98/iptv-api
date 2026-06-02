from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Channel(Base):
    __tablename__ = "channels"

    id = Column(String(50), primary_key=True)
    provider_id = Column(String(50), nullable=True)
    nombre = Column(String(255), nullable=False)
    nombre_normalizado = Column(String(255), nullable=True)
    logo = Column(Text, nullable=True)
    grupo = Column(String(255), nullable=True)
    grupo_normalizado = Column(String(255), nullable=True)
    country = Column(String(10), nullable=True)
    url = Column(Text, nullable=False)
    numero = Column(Integer, nullable=True)
    tvg_id = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=True)


class ChannelFavorite(Base):
    __tablename__ = "channel_favorites"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    channel_provider_id = Column(String(100), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (PrimaryKeyConstraint("user_id", "channel_provider_id"),)
