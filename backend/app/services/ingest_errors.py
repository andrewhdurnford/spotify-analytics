from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx


class IngestErrorCategory(StrEnum):
    API = "api"
    DATABASE = "database"
    RATE_LIMIT = "rate_limit"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IngestIssue:
    category: IngestErrorCategory
    code: str
    message: str
    operation: str
    identifiers: tuple[str, ...] = ()
    status_code: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def log_extra(self) -> dict[str, Any]:
        return {
            "ingest_error_category": self.category.value,
            "ingest_error_code": self.code,
            "ingest_operation": self.operation,
            "ingest_identifiers": list(self.identifiers),
            "ingest_status_code": self.status_code,
            "ingest_details": self.details,
        }


IssueReporter = Callable[[IngestIssue], None]


def spotify_api_issue(
    *,
    operation: str,
    message: str,
    exc: Exception,
    identifiers: list[str] | tuple[str, ...] = (),
) -> IngestIssue:
    status_code = _http_status_code(exc)
    if status_code == 429:
        category = IngestErrorCategory.RATE_LIMIT
        code = "SPOTIFY_RATE_LIMITED"
    elif status_code:
        category = IngestErrorCategory.API
        code = "SPOTIFY_API_ERROR"
    else:
        category = IngestErrorCategory.UNKNOWN
        code = "SPOTIFY_REQUEST_FAILED"

    return IngestIssue(
        category=category,
        code=code,
        message=message,
        operation=operation,
        identifiers=tuple(identifiers),
        status_code=status_code,
        details={"exception": str(exc)},
    )


def _http_status_code(exc: Exception) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    return None
