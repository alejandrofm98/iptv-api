from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text

from app.database import Base


class Config(Base):
    __tablename__ = "config"

    key = Column(String, primary_key=True)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=datetime.utcnow)


class SyncMetadata(Base):
    __tablename__ = "sync_metadata"

    id = Column(String, primary_key=True)
    field = Column(Text, nullable=True)
    value = Column(Text, nullable=True)
