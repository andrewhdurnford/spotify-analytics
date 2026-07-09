from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

SQLALCHEMY_DATABASE_URL = settings.database_url


def build_connect_args(
    database_url: str,
    statement_timeout_ms: int,
    connect_timeout_seconds: int,
) -> dict:
    """Build psycopg2 connect_args enforcing connection + statement timeouts.

    A hung/slow query or a stalled TCP handshake to the database shouldn't be
    able to tie up a worker (or a whole connection-pool slot) indefinitely.
    SQLite (used in tests) doesn't understand these psycopg2-specific
    options, so it gets an empty dict.
    """
    if database_url.startswith("sqlite"):
        return {}
    return {
        "connect_timeout": connect_timeout_seconds,
        "options": f"-c statement_timeout={statement_timeout_ms}",
    }


_connect_args = build_connect_args(
    SQLALCHEMY_DATABASE_URL,
    settings.db_statement_timeout_ms,
    settings.db_connect_timeout_seconds,
)

# Supabase needs different pool settings
if "supabase" in SQLALCHEMY_DATABASE_URL:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        connect_args=_connect_args,
    )
else:
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_pre_ping=True,
        connect_args=_connect_args,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
