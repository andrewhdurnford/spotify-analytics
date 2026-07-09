"""Unit tests for database connection/timeout configuration."""

from app.database import build_connect_args


class TestBuildConnectArgs:
    def test_sqlite_gets_no_connect_args(self):
        assert build_connect_args("sqlite://", 10_000, 5) == {}

    def test_postgres_gets_connect_and_statement_timeout(self):
        args = build_connect_args("postgresql://user:pw@host/db?sslmode=require", 10_000, 5)
        assert args["connect_timeout"] == 5
        assert args["options"] == "-c statement_timeout=10000"

    def test_supabase_url_also_gets_timeouts(self):
        args = build_connect_args(
            "postgresql://user:pw@db.supabase.co/db?sslmode=require", 7_500, 3
        )
        assert args["connect_timeout"] == 3
        assert args["options"] == "-c statement_timeout=7500"
