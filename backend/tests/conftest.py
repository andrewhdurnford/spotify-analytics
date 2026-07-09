"""Shared test fixtures.

Uses an in-memory SQLite database so tests run fast and don't need
a real PostgreSQL instance.
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/test?sslmode=require")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.dashboard_cache import dashboard_cache
from app.services.rate_limit import reset_all_rate_limiters
from app.services.timezone import sqlite_local_timestamp

# In-memory SQLite for tests ------------------------------------------------
engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def register_timezone_function(dbapi_connection, _connection_record):
    dbapi_connection.create_function(
        "spotify_local_timestamp",
        1,
        sqlite_local_timestamp,
    )


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def setup_database():
    """Create tables before each test and drop them after."""
    dashboard_cache.clear()
    reset_all_rate_limiters()
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)
    dashboard_cache.clear()
    reset_all_rate_limiters()


@pytest.fixture()
def db():
    """Yield a DB session for direct use in unit tests."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    """FastAPI TestClient wired to the test database."""

    def _override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
