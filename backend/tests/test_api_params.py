"""Unit tests for shared request-parameter parsing/bounds."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.api.params import MIN_ALLOWED_DATE, parse_date_param

FIXED_NOW = datetime(2026, 7, 8, 12, 0, 0)


class TestParseDateParam:
    def test_none_returns_now(self):
        result = parse_date_param(None, now=FIXED_NOW)
        assert result == FIXED_NOW

    def test_valid_date_within_range(self):
        result = parse_date_param("2025-01-15", now=FIXED_NOW)
        assert result == datetime(2025, 1, 15)

    def test_invalid_format_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            parse_date_param("not-a-date", now=FIXED_NOW)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"]["code"] == "INVALID_DATE"

    def test_timezone_aware_timestamp_is_rejected_with_400(self):
        with pytest.raises(HTTPException) as exc_info:
            parse_date_param("2025-01-15T00:00:00+00:00", now=FIXED_NOW)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"]["code"] == "INVALID_DATE"

    def test_date_before_min_allowed_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            parse_date_param("1999-12-31", now=FIXED_NOW)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"]["code"] == "INVALID_DATE"

    def test_date_at_min_allowed_boundary_is_accepted(self):
        result = parse_date_param(MIN_ALLOWED_DATE.date().isoformat(), now=FIXED_NOW)
        assert result == MIN_ALLOWED_DATE

    def test_far_future_date_raises_400(self):
        with pytest.raises(HTTPException) as exc_info:
            parse_date_param("2099-01-01", now=FIXED_NOW)
        assert exc_info.value.status_code == 400
        assert exc_info.value.detail["error"]["code"] == "INVALID_DATE"

    def test_slightly_future_date_within_grace_window_is_accepted(self):
        result = parse_date_param("2026-07-09", now=FIXED_NOW)
        assert result == datetime(2026, 7, 9)

    def test_date_far_beyond_grace_window_raises_400(self):
        with pytest.raises(HTTPException):
            parse_date_param("2026-07-11", now=FIXED_NOW)
