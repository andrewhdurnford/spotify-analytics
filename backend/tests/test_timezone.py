from datetime import datetime

from sqlalchemy import column, func, select
from sqlalchemy.dialects import postgresql

from app.services.timezone import (
    display_naive_to_utc,
    display_timestamp,
    utc_naive_to_display,
)


def test_utc_timestamp_converts_to_previous_toronto_day():
    assert utc_naive_to_display(datetime(2026, 7, 9, 2, 5)) == datetime(2026, 7, 8, 22, 5)


def test_toronto_boundaries_follow_daylight_saving_time():
    assert display_naive_to_utc(datetime(2026, 7, 1)) == datetime(2026, 7, 1, 4)
    assert display_naive_to_utc(datetime(2026, 1, 1)) == datetime(2026, 1, 1, 5)


def test_postgresql_expression_treats_stored_timestamp_as_utc():
    local = display_timestamp(column("played_at"))
    statement = select(func.date(local))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "played_at AT TIME ZONE 'UTC'" in sql
    assert "timezone('America/Toronto'" in sql
