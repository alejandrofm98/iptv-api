"""User Service v2 — uses SQLAlchemy repository."""
import bcrypt
from datetime import datetime
from typing import Optional, List, Tuple

from sqlalchemy.orm import Session

from app.repositories.user_repo import UserRepository
from app.repositories.session_repo import SessionRepository


class UserServiceV2:
    def __init__(self, session: Session):
        self.session = session
        self.user_repo = UserRepository(session)
        self.session_repo = SessionRepository(session)

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    @staticmethod
    def _verify_password(password: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
        except Exception:
            return False

    def get_user(self, user_id: str) -> Optional[dict]:
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return None
        result = {
            "id": str(user.id),
            "username": user.username,
            "max_connections": user.max_connections,
            "is_active": user.is_active,
            "role": user.role,
            "expires_at": user.expires_at.isoformat() if user.expires_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
            "active_devices": self.session_repo.count_by_user(str(user.id)),
        }
        return result

    def get_by_username(self, username: str) -> Optional[dict]:
        user = self.user_repo.get_by_username(username)
        if not user:
            return None
        return {
            "id": str(user.id),
            "username": user.username,
            "password_hash": user.password_hash,
            "max_connections": user.max_connections,
            "is_active": user.is_active,
            "role": user.role,
            "expires_at": user.expires_at,
        }

    def validate_credentials(self, username: str, password: str):
        from utils.models import AuthResult
        user = self.get_by_username(username)
        if not user:
            return AuthResult(valid=False, user_id="", message="Usuario no encontrado")
        if not user.get("is_active", True):
            return AuthResult(valid=False, user_id="", message="Usuario desactivado")
        if user.get("expires_at") and user["expires_at"] < datetime.utcnow():
            return AuthResult(valid=False, user_id="", message="Usuario expirado")
        if not self._verify_password(password, user.get("password_hash", "")):
            return AuthResult(valid=False, user_id="", message="Contraseña incorrecta")
        active = self.session_repo.count_by_user(user["id"])
        return AuthResult(
            valid=True,
            user_id=user["id"],
            username=user["username"],
            message="OK",
            can_connect=active < user.get("max_connections", 2),
            current_devices=active,
            max_devices=user.get("max_connections", 2),
        )

    def create_user(self, username: str, password: str, **kwargs) -> dict:
        existing = self.user_repo.get_by_username(username)
        if existing:
            raise ValueError(f"El usuario '{username}' ya existe")
        user = self.user_repo.create(
            username=username,
            password_hash=self._hash_password(password),
            **kwargs,
        )
        return {
            "id": str(user.id),
            "username": user.username,
            "max_connections": user.max_connections,
            "is_active": user.is_active,
            "role": user.role,
        }

    def create_user_from_model(self, user_data) -> dict:
        return self.create_user(
            username=user_data.username,
            password=user_data.password,
            max_connections=user_data.max_connections,
            is_active=True,
            role=getattr(user_data, 'role', 'user'),
            expires_at=user_data.expires_at.isoformat() if user_data.expires_at else None,
        )

    def update_user(self, user_id: str, user_data) -> Optional[dict]:
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return None
        update_dict = {}
        if user_data.password is not None:
            update_dict["password_hash"] = self._hash_password(user_data.password)
        if user_data.max_connections is not None:
            update_dict["max_connections"] = user_data.max_connections
        if user_data.is_active is not None:
            update_dict["is_active"] = user_data.is_active
        if user_data.expires_at is not None:
            update_dict["expires_at"] = user_data.expires_at
        if hasattr(user_data, 'role') and user_data.role is not None:
            update_dict["role"] = user_data.role
        if update_dict:
            for key, val in update_dict.items():
                setattr(user, key, val)
            self.session.flush()
        return self.get_user(user_id)

    def delete_user(self, user_id: str) -> bool:
        user = self.user_repo.get_by_id(user_id)
        if not user:
            return False
        self.session.delete(user)
        self.session.flush()
        return True

    def list_users(self, page: int = 1, page_size: int = 20) -> Tuple[List[dict], int]:
        users, total = self.user_repo.list_paginated(page, page_size)
        result = []
        for u in users:
            d = {
                "id": str(u.id),
                "username": u.username,
                "max_connections": u.max_connections,
                "is_active": u.is_active,
                "role": u.role,
                "expires_at": u.expires_at.isoformat() if u.expires_at else None,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "active_devices": self.session_repo.count_by_user(str(u.id)),
            }
            result.append(d)
        return result, total
