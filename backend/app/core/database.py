from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from app.core.config import settings

engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

# Dedicated engine for executing user queries with strict statement timeout
readonly_url = settings.READONLY_DATABASE_URL or settings.DATABASE_URL
query_engine = create_engine(
    readonly_url,
    pool_pre_ping=True,
    connect_args={
        "options": f"-c statement_timeout={settings.QUERY_TIMEOUT_MS}"
    } if "postgresql" in readonly_url else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
