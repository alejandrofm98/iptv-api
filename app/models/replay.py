import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.database import Base


class Replay(Base):
    __tablename__ = "replays"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(Text, nullable=False, unique=True)
    source_site = Column(Text, nullable=False, default="watch-wrestling.eu")
    source_id = Column(BigInteger, nullable=True, unique=True)
    category = Column(Text, nullable=True)
    title = Column(Text, nullable=False)
    event_name = Column(Text, nullable=True)
    event_type = Column(Text, nullable=True)
    event_date = Column(DateTime, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    modified_at = Column(DateTime(timezone=True), nullable=True)
    post_url = Column(Text, nullable=False)
    featured_image_url = Column(Text, nullable=True)
    excerpt = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    video_sources = Column(JSONB, nullable=False, default=list)
    match_card = Column(JSONB, nullable=False, default=list)
    raw_payload = Column(JSONB, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at = Column(
        DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow
    )
