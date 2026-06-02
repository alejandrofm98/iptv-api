import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class WatchProgress(Base):
    __tablename__ = "watch_progress"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    content_id = Column(String(100), nullable=False)
    content_type = Column(String(20), nullable=False)
    position_ms = Column(BigInteger, default=0, nullable=False)
    duration_ms = Column(BigInteger, default=0, nullable=False)
    series_name = Column(String(255), nullable=True)
    season_number = Column(Integer, nullable=True)
    episode_number = Column(Integer, nullable=True)
    title = Column(String(255), nullable=False, default="")
    image_url = Column(String, nullable=False, default="")
    last_watched_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    is_watched = Column(Boolean, nullable=False, default=False)

    __table_args__ = (UniqueConstraint("user_id", "content_id"),)
