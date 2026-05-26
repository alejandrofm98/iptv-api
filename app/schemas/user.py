from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    username: str
    max_connections: int
    is_active: bool
    role: str
    expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str
    password: str
    max_connections: int = 2
    is_active: bool = True
    role: str = "user"
    expires_at: Optional[datetime] = None


class UserUpdate(BaseModel):
    password: Optional[str] = None
    max_connections: Optional[int] = None
    is_active: Optional[bool] = None
    role: Optional[str] = None
    expires_at: Optional[datetime] = None
