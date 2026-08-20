from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Artist, Track
from app.services.ingest_errors import IssueReporter, spotify_api_issue

ArtistDetailsFetcher = Callable[[str], Awaitable[dict[str, Any] | None]]
Warn = Callable[[str], None]


def ensure_track(db: Session, track: dict[str, Any], first_played_at: datetime) -> int:
    """Create a Track row from a Spotify track object if one doesn't exist.

    Shared by app.services.recent_plays_sync -- any caller that already has
    a full Spotify track payload (not just an ID) can enrich Track/Artist
    immediately instead of waiting on the separate metadata-backfill script
    historical import relies on.
    """
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


async def ensure_artists(
    db: Session,
    track: dict[str, Any],
    *,
    fetch_artist_details: ArtistDetailsFetcher,
    warn: Warn | None = None,
    report_issue: IssueReporter | None = None,
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
