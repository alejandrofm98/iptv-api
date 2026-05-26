"""Device/Session Service v2 — uses SQLAlchemy repository."""
from datetime import datetime
from typing import Optional, Tuple, List

from sqlalchemy.orm import Session

from app.repositories.session_repo import SessionRepository


class DeviceServiceV2:
    def __init__(self, session: Session):
        self.session = session
        self.session_repo = SessionRepository(session)

    def register_or_update_session(
        self, user_id: str, user_agent: str, ip_address: str, max_connections: int,
    ) -> Tuple[bool, str, Optional[dict]]:
        device_id = self._device_id_from_ua(user_agent, ip_address)
        active = self.session_repo.count_by_user(user_id)
        if active >= max_connections:
            return False, "Límite de dispositivos alcanzado", None

        session = self.session_repo.upsert(user_id, device_id, {
            "device_name": self._device_name_from_ua(user_agent),
            "device_type": self._device_type_from_ua(user_agent),
            "ip_address": ip_address,
            "user_agent": user_agent,
            "last_activity": datetime.utcnow(),
        })
        return True, "Sesión registrada", {
            "id": str(session.id),
            "device_id": session.device_id,
            "device_name": session.device_name,
            "device_type": session.device_type,
            "ip_address": session.ip_address,
        }

    def get_user_devices(self, user_id: str) -> List[dict]:
        """Alias for compatibility with old DeviceService interface."""
        return self.get_active_sessions(user_id)

    def get_active_sessions(self, user_id: str) -> List[dict]:
        sessions = self.session_repo.get_by_user(user_id)
        return [
            {
                "id": str(s.id),
                "device_id": s.device_id,
                "device_name": s.device_name,
                "device_type": s.device_type,
                "ip_address": s.ip_address,
                "last_activity": s.last_activity.isoformat() if s.last_activity else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sessions
        ]

    def disconnect_device(self, user_id: str, device_id: str) -> bool:
        """Alias for compatibility with old DeviceService interface."""
        return self.delete_session(user_id, device_id)

    def disconnect_all_devices(self, user_id: str) -> int:
        """Alias for compatibility with old DeviceService interface."""
        return self.delete_all_user_sessions(user_id)

    def delete_session(self, user_id: str, device_id: str) -> bool:
        return self.session_repo.delete_by_user_and_device(user_id, device_id)

    def delete_all_user_sessions(self, user_id: str) -> int:
        return self.session_repo.delete_by_user(user_id)

    def cleanup_inactive_sessions(self, timeout_minutes: int = 30) -> int:
        return self.session_repo.cleanup_inactive(timeout_minutes)

    def count_user_sessions(self, user_id: str) -> int:
        return self.session_repo.count_by_user(user_id)

    def get_all_sessions(self, limit: int = 100) -> List[dict]:
        return self.session_repo.list_all_with_users(limit)

    @staticmethod
    def _device_id_from_ua(user_agent: str, ip: str) -> str:
        import hashlib
        raw = f"{user_agent}|{ip}"
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _device_name_from_ua(user_agent: str) -> str:
        if "Android" in user_agent:
            return "Android"
        if "iPhone" in user_agent or "iOS" in user_agent:
            return "iOS"
        if "SmartTV" in user_agent or "TV" in user_agent:
            return "Smart TV"
        return user_agent[:50] if user_agent else "Unknown"

    @staticmethod
    def _device_type_from_ua(user_agent: str) -> str:
        if "Mobile" in user_agent or "Android" in user_agent:
            return "mobile"
        if "TV" in user_agent:
            return "tv"
        return "web"
