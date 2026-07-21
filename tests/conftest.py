"""Fixtures compartidos para tests de iptv-api.

Reemplaza el hack anterior de sys.modules['psycopg2'] con fixtures limpios
y aislables por test. Todos los tests existentes deben poder importar
``conftest_fakes`` y ``client`` y ``app_settings``.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Add src/ directory to sys.path for true src/ layout
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.fixture(autouse=True)
def _install_psycopg2_stub() -> Iterator[None]:
    """Stub de psycopg2 para que ``iptv_api.core.config`` se pueda importar."""
    fake = types.ModuleType("psycopg2")
    fake.connect = MagicMock(return_value=MagicMock())  # type: ignore[attr-defined]
    fake.extras = MagicMock()  # type: ignore[attr-defined]
    fake.sql = MagicMock()  # type: ignore[attr-defined]
    sys.modules.setdefault("psycopg2", fake)
    yield
    # No limpiamos: pytest puede reusar el modulo entre tests


@pytest.fixture
def app_settings(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Settings minimos para tests sin tocar Supabase ni Postgres real.

    Devuelve un MagicMock que se puede pasar a servicios que esperan
    ``Settings``. Cada test puede sobreescribir atributos especificos.
    """
    from iptv_api.core.config import Settings

    settings = MagicMock(spec=Settings)
    settings.SUPABASE_URL = "https://test.supabase.co"
    settings.SUPABASE_KEY = "test-key"
    settings.API_SECRET_KEY = "test-secret"
    settings.JWT_SECRET = "test-jwt-secret"
    settings.PUBLIC_DOMAIN = "http://localhost:3010"
    settings.M3U_DIR = "/tmp/iptv-test-m3u"
    settings.is_valid.return_value = True
    return settings


@pytest.fixture
def db_session() -> Iterator[MagicMock]:
    """Sesion SQLAlchemy falsa para tests unitarios de servicios.

    Cada test obtiene un MagicMock fresco. Para tests de integracion
    que necesiten SQLite en memoria, sobreescribir este fixture localmente.
    """
    session = MagicMock()
    yield session


@pytest.fixture
def client(db_session: MagicMock, app_settings: MagicMock) -> Iterator[Any]:
    """TestClient de FastAPI con dependencias overridden.

    Hace todo lo que hacia ``test_android_catalog_api.py`` antes:
    stub de psycopg2 (via ``_install_psycopg2_stub``), settings validos,
    y ``get_db`` que devuelve la ``db_session`` del fixture.
    """
    from fastapi.testclient import TestClient

    from iptv_api.core import dependencies
    from iptv_api.main import app

    def _override_get_db() -> Iterator[MagicMock]:
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[dependencies.get_db] = _override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def stub_content_service() -> Any:
    """Stub del servicio de contenido usado en tests Android.

    Replica exactamente el ``StubContentService`` que vivia dentro de
    ``test_android_catalog_api.py``. Muevelo aqui para reutilizar.
    """
    from iptv_api.services.content_service import ContentServiceV2  # noqa: F401

    class StubContentService:
        """Devuelve datos fijos sin tocar DB."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def get_android_content_list(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "channels": [],
                "movies": [],
                "series": [],
                "page": 1,
                "page_size": 60,
                "total": 0,
            }

    from iptv_api.core import dependencies as _dep

    app = _get_app()
    app.dependency_overrides[_dep.get_content_service_v2] = lambda: StubContentService()
    return StubContentService


def _get_app() -> Any:
    from iptv_api.main import app

    return app
