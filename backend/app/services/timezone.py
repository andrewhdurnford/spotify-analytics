"""Timezone helpers for UTC storage and Toronto-facing analytics."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import DateTime

DISPLAY_TIMEZONE_NAME = "America/Toronto"
DISPLAY_TIMEZONE = ZoneInfo(DISPLAY_TIMEZONE_NAME)


class display_timestamp(FunctionElement):
    """Convert a naive UTC database timestamp to the display timezone."""

    type = DateTime()
    inherit_cache = True


@compiles(display_timestamp, "postgresql")
def _compile_postgresql(element, compiler, **kwargs):
    value = compiler.process(element.clauses, **kwargs)
    return f"timezone('{DISPLAY_TIMEZONE_NAME}', {value} AT TIME ZONE 'UTC')"


@compiles(display_timestamp, "sqlite")
def _compile_sqlite(element, compiler, **kwargs):
    value = compiler.process(element.clauses, **kwargs)
    return f"spotify_local_timestamp({value})"


def utc_naive_to_display(value: datetime) -> datetime:
    """Convert the database's naive UTC representation to naive Toronto time."""
    return value.replace(tzinfo=UTC).astimezone(DISPLAY_TIMEZONE).replace(tzinfo=None)


def display_naive_to_utc(value: datetime) -> datetime:
    """Convert a naive Toronto calendar timestamp to the database UTC form."""
    return value.replace(tzinfo=DISPLAY_TIMEZONE).astimezone(UTC).replace(tzinfo=None)


def sqlite_local_timestamp(value: str | datetime | None) -> str | None:
    """SQLite UDF used by tests to mirror the PostgreSQL timezone expression."""
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return utc_naive_to_display(parsed).isoformat(sep=" ")
