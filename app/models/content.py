import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class MovieMetadata(Base):
    __tablename__ = "movies_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tmdb_id = Column(String(20), unique=True, nullable=True)
    title = Column(String(255), nullable=True)
    original_title = Column(String(255), nullable=True)
    overview_es = Column(Text, nullable=True)
    overview_en = Column(Text, nullable=True)
    genres = Column(ARRAY(String), nullable=True)  # type: ignore  # mypy: ARRAY typing
    vote_average = Column(Float, nullable=True)
    vote_count = Column(Integer, nullable=True)
    poster_path = Column(String(255), nullable=True)
    backdrop_path = Column(String(255), nullable=True)
    release_date = Column(Date, nullable=True)
    year = Column(Integer, nullable=True)
    runtime_minutes = Column(Integer, nullable=True)
    tagline = Column(String(500), nullable=True)
    popularity = Column(Float, nullable=True)
    status = Column(String(50), nullable=True)
    imdb_id = Column(String(20), nullable=True)
    tmdb_data = Column(JSONB, nullable=True)
    scraped_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class MovieCatalog(Base):
    __tablename__ = "movies_catalog"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(Text, nullable=False)
    provider_id = Column(String(50), nullable=True)
    tmdb_id = Column(
        String(20),
        ForeignKey("movies_metadata.tmdb_id", ondelete="SET NULL"),
        nullable=True,
    )
    nombre_dedup_key = Column(Text, nullable=True)
    canonical_key = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    countries = Column(ARRAY(String(10)), nullable=True)
    group_normalizado = Column(Text, nullable=True)
    logo = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    metadata_row = relationship(
        "MovieMetadata",
        primaryjoin="MovieCatalog.tmdb_id == MovieMetadata.tmdb_id",
        uselist=False,
    )
    streams = relationship("MovieStream", back_populates="movie", cascade="all, delete-orphan")


class MovieStream(Base):
    __tablename__ = "movie_streams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    movie_id = Column(
        UUID(as_uuid=True),
        ForeignKey("movies_catalog.id", ondelete="CASCADE"),
        nullable=False,
    )
    country = Column(String(10), nullable=False)
    quality = Column(String(10), nullable=True)
    provider_id = Column(String(50), nullable=True)
    stream_url = Column(Text, nullable=False)
    url = Column(Text, nullable=True)
    label = Column(Text, nullable=True)
    numero = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    movie = relationship("MovieCatalog", back_populates="streams")
