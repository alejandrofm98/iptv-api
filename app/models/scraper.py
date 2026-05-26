import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class ScraperFailure(Base):
    __tablename__ = "scraper_failures"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider_id = Column(String(50), nullable=True)
    series_key = Column(String(255), nullable=True)
    title = Column(Text, nullable=False)
    year = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    failed_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    retry_count = Column(Integer, default=1)
    last_retry_at = Column(DateTime(timezone=True), default=datetime.utcnow)
