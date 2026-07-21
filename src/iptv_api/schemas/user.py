from datetime import datetime

from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    username: str
    max_connections: int
    is_active: bool
    role: str
    expires_at: datetime | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str
    password: str
    max_connections: int = 2
    is_active: bool = True
    role: str = "user"
    expires_at: datetime | None = None


class UserUpdate(BaseModel):
    password: str | None = None
    max_connections: int | None = None
    is_active: bool | None = None
    role: str | None = None
    expires_at: datetime | None = None
