from iptv_db.models.base import Base  # noqa: F401 — re-export, used by repos
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from iptv_api.core.config import get_settings

settings = get_settings()
engine: Engine | None = None
SessionLocal: sessionmaker[Session] | None = None


def _get_session_factory() -> sessionmaker[Session]:
    """Crea el engine solo cuando la aplicación necesita una sesión real."""
    global SessionLocal, engine

    if SessionLocal is not None:
        return SessionLocal

    database_url = settings.get_postgres_connection_string()
    engine = create_engine(
        database_url,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 10},
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal


def get_session() -> Session:
    """Abre una sesión y valida la configuración al usarla, no al importar."""
    return _get_session_factory()()
