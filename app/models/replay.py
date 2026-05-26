import uuid

from sqlalchemy import Column, Date, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class Replay(Base):
    __tablename__ = "replays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(Text, nullable=False, unique=True)
    source_site = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    event_name = Column(Text, nullable=True)
    event_type = Column(Text, nullable=True)
    event_date = Column(Date, nullable=True)
    post_url = Column(Text, nullable=False)
    featured_image_url = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    video_sources = Column(JSONB, nullable=False)
    match_card = Column(JSONB, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)
