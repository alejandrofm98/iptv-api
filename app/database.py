from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from utils.config import get_settings

settings = get_settings()
DATABASE_URL = settings.get_postgres_connection_string()

engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
