import httpx

from app.services.ingest_errors import IngestErrorCategory, spotify_api_issue


def test_spotify_api_issue_classifies_rate_limits():
    request = httpx.Request("GET", "https://api.spotify.com/v1/tracks")
    response = httpx.Response(429, request=request)
    exc = httpx.HTTPStatusError("rate limited", request=request, response=response)

    issue = spotify_api_issue(
        operation="track_metadata",
        message="spotify track batch fetch failed: rate limited",
        exc=exc,
        identifiers=["track-1"],
    )

    assert issue.category == IngestErrorCategory.RATE_LIMIT
    assert issue.code == "SPOTIFY_RATE_LIMITED"
    assert issue.status_code == 429
    assert issue.identifiers == ("track-1",)
    assert issue.log_extra()["ingest_error_category"] == "rate_limit"


def test_spotify_api_issue_classifies_other_http_errors():
    request = httpx.Request("GET", "https://api.spotify.com/v1/artists")
    response = httpx.Response(500, request=request)
    exc = httpx.HTTPStatusError("server error", request=request, response=response)

    issue = spotify_api_issue(
        operation="artist_metadata",
        message="spotify artist batch fetch failed: server error",
        exc=exc,
    )

    assert issue.category == IngestErrorCategory.API
    assert issue.code == "SPOTIFY_API_ERROR"
    assert issue.status_code == 500


def test_spotify_api_issue_classifies_request_failures_without_status():
    issue = spotify_api_issue(
        operation="artist_metadata",
        message="spotify artist batch fetch failed: boom",
        exc=RuntimeError("boom"),
    )

    assert issue.category == IngestErrorCategory.UNKNOWN
    assert issue.code == "SPOTIFY_REQUEST_FAILED"
    assert issue.status_code is None
