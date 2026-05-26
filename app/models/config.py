from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP

from app.database import Base


class Config(Base):
    __tablename__ = "config"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class SyncMetadata(Base):
    __tablename__ = "sync_metadata"

    id = Column(String(50), primary_key=True)
    ultima_actualizacion = Column(TIMESTAMP, nullable=True)
    total_canales = Column(Integer, nullable=True)
    total_movies = Column(Integer, nullable=True)
    total_series = Column(Integer, nullable=True)
    m3u_template_path = Column(Text, nullable=True)
    m3u_template_filename = Column(Text, nullable=True)
    m3u_size_mb = Column(Numeric(10, 2), nullable=True)
    channels_con_logo = Column(Integer, nullable=True)
    channels_sin_logo = Column(Integer, nullable=True)
    movies_con_logo = Column(Integer, nullable=True)
    movies_sin_logo = Column(Integer, nullable=True)
    series_con_logo = Column(Integer, nullable=True)
    series_sin_logo = Column(Integer, nullable=True)
    created_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, nullable=True)
    channels_generated_at = Column(TIMESTAMP, nullable=True)
    channels_json_size_mb = Column(Numeric(10, 2), nullable=True)
    movies_generated_at = Column(TIMESTAMP, nullable=True)
    movies_json_size_mb = Column(Numeric(10, 2), nullable=True)
    series_generated_at = Column(TIMESTAMP, nullable=True)
    series_json_size_mb = Column(Numeric(10, 2), nullable=True)
