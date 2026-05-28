from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track
from app.services.ingest_errors import IssueReporter, spotify_api_issue

DUPLICATE_GRACE = timedelta(seconds=30)

ArtistDetailsFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]
Warn = Callable[[str], None]


@dataclass(frozen=True)
class StoreCompletedPlayResult:
    stored: bool
    duplicate: bool
    new_tracks: int
    new_artists: int


async def store_completed_play(
    db: Session,
    track: dict[str, Any],
    *,
    progress_ms: int,
    played_at: datetime | None = None,
    fetch_artist_details: ArtistDetailsFetcher,
    warn: Warn | None = None,
    report_issue: IssueReporter | None = None,
) -> StoreCompletedPlayResult:
    """Persist a completed current-playback observation."""
    played_at = played_at or datetime.now(UTC).replace(tzinfo=None)

    if _has_duplicate_stream(db, track["id"], played_at, progress_ms):
        return StoreCompletedPlayResult(
            stored=False,
            duplicate=True,
            new_tracks=0,
            new_artists=0,
        )

    new_tracks = _ensure_track(db, track, played_at)
    new_artists = await _ensure_artists(
        db,
        track,
        fetch_artist_details=fetch_artist_details,
        warn=warn,
        report_issue=report_issue,
    )
    db.add(_stream_from_playback(track, played_at))
    db.commit()

    return StoreCompletedPlayResult(
        stored=True,
        duplicate=False,
        new_tracks=new_tracks,
        new_artists=new_artists,
    )


def _has_duplicate_stream(
    db: Session,
    track_id: str,
    played_at: datetime,
    progress_ms: int,
) -> bool:
    estimated_started_at = played_at - timedelta(milliseconds=progress_ms)
    duplicate_start = estimated_started_at - DUPLICATE_GRACE
    duplicate_end = played_at + DUPLICATE_GRACE

    existing_stream = db.execute(
        select(Stream).where(
            Stream.track_id == track_id,
            Stream.played_at >= duplicate_start,
            Stream.played_at <= duplicate_end,
        )
    ).scalar_one_or_none()

    return existing_stream is not None


def _ensure_track(db: Session, track: dict[str, Any], first_played_at: datetime) -> int:
    track_record = db.execute(
        select(Track).where(Track.spotify_id == track["id"])
    ).scalar_one_or_none()
    if track_record:
        return 0

    album_image = None
    if track.get("album") and track["album"].get("images"):
        album_image = track["album"]["images"][0]["url"]

    db.add(
        Track(
            spotify_id=track["id"],
            name=track["name"],
            artist_name=track["artists"][0]["name"] if track.get("artists") else None,
            album_name=track.get("album", {}).get("name"),
            album_image=album_image,
            duration_ms=track.get("duration_ms"),
            popularity=track.get("popularity"),
            first_played_at=first_played_at,
        )
    )
    db.flush()
    return 1


async def _ensure_artists(
    db: Session,
    track: dict[str, Any],
    *,
    fetch_artist_details: ArtistDetailsFetcher,
    warn: Warn | None,
    report_issue: IssueReporter | None,
) -> int:
    new_artists = 0
    for artist_data in track.get("artists") or []:
        artist_record = db.execute(
            select(Artist).where(Artist.spotify_id == artist_data["id"])
        ).scalar_one_or_none()
        if artist_record:
            continue

        try:
            artist_full = await fetch_artist_details(artist_data["id"])
        except Exception as exc:
            issue = spotify_api_issue(
                operation="playback_artist_metadata",
                message=f"Error fetching artist {artist_data['id']}: {exc}",
                exc=exc,
                identifiers=[artist_data["id"]],
            )
            if report_issue:
                report_issue(issue)
            if warn:
                warn(issue.message)
            continue

        if not artist_full:
            continue

        image_url = None
        if artist_full.get("images"):
            image_url = artist_full["images"][0]["url"]

        db.add(
            Artist(
                spotify_id=artist_data["id"],
                name=artist_data["name"],
                genres=artist_full.get("genres", []),
                popularity=artist_full.get("popularity"),
                image_url=image_url,
            )
        )
        db.flush()
        new_artists += 1

    return new_artists


def _stream_from_playback(track: dict[str, Any], played_at: datetime) -> Stream:
    return Stream(
        track_name=track["name"],
        artist_name=track["artists"][0]["name"] if track.get("artists") else "Unknown",
        album_name=track.get("album", {}).get("name"),
        played_at=played_at,
        duration_ms=track.get("duration_ms"),
        track_id=track["id"],
        artist_id=track["artists"][0]["id"] if track.get("artists") else None,
    )
