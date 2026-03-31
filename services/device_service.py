"""
Servicio de gestión de dispositivos y sesiones
"""
import hashlib
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple

from utils.config import get_settings
from utils.models import DeviceType
from services.postgres_service import PostgresService


class DeviceService:
    """Servicio para gestión de dispositivos y sesiones"""

    def __init__(self, pg_service: PostgresService):
        self.pg = pg_service
        self.settings = get_settings()

    def _generate_device_id(self, ip_address: str) -> str:
        """Genera un ID único basado solo en la IP (cada IP = 1 dispositivo)"""
        return hashlib.sha256(ip_address.encode()).hexdigest()[:32]

    def _parse_device_info(self, user_agent: str) -> Tuple[str, DeviceType]:
        """
        Parsea el User-Agent para obtener nombre y tipo de dispositivo

        Returns:
            (device_name, device_type)
        """
        ua_lower = user_agent.lower()

        iptv_apps = {
            'tivimate': ('TiviMate', DeviceType.TV),
            'iptv smarters': ('IPTV Smarters', DeviceType.MOBILE),
            'smarters': ('IPTV Smarters', DeviceType.MOBILE),
            'xciptv': ('XCIPTV', DeviceType.MOBILE),
            'ott navigator': ('OTT Navigator', DeviceType.TV),
            'perfect player': ('Perfect Player', DeviceType.TV),
            'kodi': ('Kodi', DeviceType.TV),
            'vlc': ('VLC Media Player', DeviceType.DESKTOP),
            'mpv': ('MPV Player', DeviceType.DESKTOP),
            'iptv pro': ('IPTV Pro', DeviceType.MOBILE),
            'gse': ('GSE Smart IPTV', DeviceType.MOBILE),
            'implayer': ('implayer', DeviceType.TV),
            'duplex': ('Duplex IPTV', DeviceType.TV),
            'ibo player': ('iBO Player', DeviceType.TV),
            'lazy iptv': ('Lazy IPTV', DeviceType.TV),
        }

        for key, (name, dtype) in iptv_apps.items():
            if key in ua_lower:
                return (name, dtype)

        tv_patterns = [
            (r'smarttv', 'Smart TV'),
            (r'smart-tv', 'Smart TV'),
            (r'webos', 'LG Smart TV'),
            (r'tizen', 'Samsung Smart TV'),
            (r'roku', 'Roku'),
            (r'fire tv', 'Amazon Fire TV'),
            (r'firetv', 'Amazon Fire TV'),
            (r'androidtv', 'Android TV'),
            (r'chromecast', 'Chromecast'),
            (r'apple\s*tv', 'Apple TV'),
            (r'playstation', 'PlayStation'),
            (r'xbox', 'Xbox'),
        ]

        for pattern, name in tv_patterns:
            if re.search(pattern, ua_lower):
                return (name, DeviceType.TV)

        mobile_patterns = [
            (r'iphone', 'iPhone'),
            (r'ipad', 'iPad'),
            (r'android.*mobile', 'Android Phone'),
            (r'android', 'Android Device'),
        ]

        for pattern, name in mobile_patterns:
            if re.search(pattern, ua_lower):
                return (name, DeviceType.MOBILE)

        browser_patterns = [
            (r'chrome', 'Chrome'),
            (r'firefox', 'Firefox'),
            (r'safari', 'Safari'),
            (r'edge', 'Edge'),
            (r'opera', 'Opera'),
        ]

        for pattern, name in browser_patterns:
            if re.search(pattern, ua_lower):
                os_name = 'Desktop'
                if 'windows' in ua_lower:
                    os_name = 'Windows'
                elif 'mac' in ua_lower:
                    os_name = 'macOS'
                elif 'linux' in ua_lower:
                    os_name = 'Linux'

                return (f"{name} - {os_name}", DeviceType.DESKTOP)

        return ('Dispositivo desconocido', DeviceType.UNKNOWN)

    def register_or_update_session(
        self,
        user_id: str,
        user_agent: str,
        ip_address: str,
        max_connections: int
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """
        Registra o actualiza una sesión de dispositivo

        Returns:
            (success, message, session_data)
        """
        device_id = self._generate_device_id(ip_address)
        device_name, device_type = self._parse_device_info(user_agent)

        existing = self.pg.get_session_by_user_and_device(user_id, device_id)

        now = datetime.utcnow().isoformat()

        if existing:
            session_data = {
                'user_id': user_id,
                'device_id': device_id,
                'device_name': device_name,
                'device_type': device_type.value,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'last_activity': now
            }
            result = self.pg.upsert_session(session_data)
            return (True, "Sesión actualizada", result)

        current_count = self.pg.count_user_sessions(user_id)

        if current_count >= max_connections:
            return (
                False,
                f"Límite de dispositivos alcanzado ({current_count}/{max_connections})",
                None
            )

        session_data = {
            'user_id': user_id,
            'device_id': device_id,
            'device_name': device_name,
            'device_type': device_type.value,
            'ip_address': ip_address,
            'user_agent': user_agent,
            'last_activity': now
        }

        result = self.pg.upsert_session(session_data)
        return (True, "Nueva sesión registrada", result)

    def get_user_devices(self, user_id: str) -> List[Dict[str, Any]]:
        """Obtiene todos los dispositivos activos de un usuario"""
        return self.pg.get_active_sessions_by_user(user_id)

    def disconnect_device(self, user_id: str, device_id: str) -> bool:
        """Desconecta un dispositivo específico"""
        return self.pg.delete_session(user_id, device_id)

    def disconnect_all_devices(self, user_id: str) -> int:
        """Desconecta todos los dispositivos de un usuario"""
        return self.pg.delete_all_user_sessions(user_id)

    def cleanup_inactive_sessions(self, timeout_minutes: int = None) -> int:
        """
        Limpia sesiones inactivas

        Returns:
            Número de sesiones eliminadas
        """
        if timeout_minutes is None:
            timeout_minutes = self.settings.session_timeout_minutes

        threshold = datetime.utcnow() - timedelta(minutes=timeout_minutes)
        return self.pg.cleanup_inactive_sessions(threshold.isoformat())

    def is_device_allowed(
        self,
        user_id: str,
        user_agent: str,
        ip_address: str,
        max_connections: int
    ) -> Tuple[bool, str]:
        """
        Verifica si un dispositivo puede conectarse

        Returns:
            (allowed, message)
        """
        device_id = self._generate_device_id(ip_address)

        existing = self.pg.get_session_by_user_and_device(user_id, device_id)
        if existing:
            return (True, "Dispositivo registrado")

        current_count = self.pg.count_user_sessions(user_id)

        if current_count >= max_connections:
            return (
                False,
                f"Límite de dispositivos alcanzado ({current_count}/{max_connections})"
            )

        return (True, "Dispositivo permitido")

    def get_all_sessions(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Obtiene todas las sesiones activas (para admin)"""
        return self.pg.get_all_sessions_with_users(limit)