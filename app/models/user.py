import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(50), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    max_connections = Column(Integer, default=2)
    is_active = Column(Boolean, default=True)
    role = Column(String(20), default="user")
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    sessions = relationship("ActiveSession", back_populates="user", cascade="all, delete-orphan")


class ActiveSession(Base):
    __tablename__ = "active_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    device_id = Column(String(64), nullable=False)
    device_name = Column(String(100), nullable=True)
    device_type = Column(String(20), default="unknown")
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String, nullable=True)
    last_activity = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    __table_args__ = (UniqueConstraint("user_id", "device_id"),)

    user = relationship("User", back_populates="sessions")
