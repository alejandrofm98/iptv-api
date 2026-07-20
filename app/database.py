from iptv_db.models.base import Base  # noqa: F401 — re-export, used by alembic & repos
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from utils.config import get_settings

settings = get_settings()
DATABASE_URL = settings.get_postgres_connection_string()

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=300,
    connect_args={"connect_timeout": 10},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
