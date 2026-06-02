import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class SeriesMetadata(Base):
    __tablename__ = "series_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tmdb_id = Column(String(20), unique=True, nullable=True)
    title = Column(String(255), nullable=True)
    original_title = Column(String(255), nullable=True)
    overview_es = Column(Text, nullable=True)
    overview_en = Column(Text, nullable=True)
    genres = Column(ARRAY(String()), nullable=True)  # type: ignore  # mypy: ARRAY typing
    vote_average = Column(Float, nullable=True)
    vote_count = Column(Integer, nullable=True)
    poster_path = Column(String(255), nullable=True)
    backdrop_path = Column(String(255), nullable=True)
    release_date = Column(Date, nullable=True)
    year = Column(Integer, nullable=True)
    tagline = Column(String(500), nullable=True)
    popularity = Column(Float, nullable=True)
    status = Column(String(50), nullable=True)
    tmdb_data = Column(JSONB, nullable=True)
    scraped_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class SeriesCatalog(Base):
    __tablename__ = "series_catalog"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(Text, nullable=False)
    series_key = Column(Text, nullable=False)
    canonical_key = Column(String, nullable=True)
    provider_id = Column(String(50), nullable=True)
    tmdb_id = Column(
        String(20),
        ForeignKey("series_metadata.tmdb_id", ondelete="SET NULL"),
        nullable=True,
    )
    nombre_dedup_key = Column(Text, nullable=True)
    year = Column(Integer, nullable=True)
    country = Column(String(10), nullable=True)
    group_normalizado = Column(Text, nullable=True)
    logo = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    metadata_row = relationship(
        "SeriesMetadata",
        primaryjoin="SeriesCatalog.tmdb_id == SeriesMetadata.tmdb_id",
        uselist=False,
    )
    episodes = relationship("SeriesEpisode", back_populates="catalog", cascade="all, delete-orphan")


class SeriesEpisode(Base):
    __tablename__ = "series_episodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_id = Column(
        UUID(as_uuid=True),
        ForeignKey("series_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    season_number = Column(Integer, nullable=False)
    episode_number = Column(Integer, nullable=False)
    title = Column(Text, nullable=True)
    overview = Column(Text, nullable=True)
    air_date = Column(Date, nullable=True)
    still_path = Column(String(255), nullable=True)
    numero = Column(Integer, nullable=True)

    __table_args__ = (UniqueConstraint("catalog_id", "season_number", "episode_number"),)

    catalog = relationship("SeriesCatalog", back_populates="episodes")
    streams = relationship("SeriesStream", back_populates="episode", cascade="all, delete-orphan")


class SeriesStream(Base):
    __tablename__ = "series_streams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    episode_id = Column(
        UUID(as_uuid=True),
        ForeignKey("series_episodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    country = Column(String(10), nullable=False)
    quality = Column(String(10), nullable=True)
    provider_id = Column(String(50), nullable=True)
    stream_url = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    label = Column(Text, nullable=True)
    numero = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    episode = relationship("SeriesEpisode", back_populates="streams")
