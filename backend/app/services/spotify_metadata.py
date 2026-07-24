from collections.abc import Callable
from typing import Any, Protocol

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import Artist, Stream, Track
from app.services.ingest_errors import IssueReporter, spotify_api_issue
from app.services.rollups import rebuild_rollups_for_tracks

BATCH_SIZE = 50
SPOTIFY_ARTISTS_URL = "https://api.spotify.com/v1/artists"
SPOTIFY_TRACKS_URL = "https://api.spotify.com/v1/tracks"


class HttpResponse(Protocol):
    def raise_for_status(self) -> None: ...

    def json(self) -> dict[str, Any]: ...


HttpGet = Callable[..., HttpResponse]
Warn = Callable[[str], None]


def get_missing_track_ids(db: Session) -> list[str]:
    stream_track_ids = (
        db.execute(
            select(Stream.track_id)
            .where(Stream.track_id.is_not(None), Stream.track_id != "")
            .distinct()
        )
        .scalars()
        .all()
    )
    if not stream_track_ids:
        return []

    existing_track_ids = set(
        db.execute(select(Track.spotify_id).where(Track.spotify_id.in_(stream_track_ids)))
        .scalars()
        .all()
    )
    return [track_id for track_id in stream_track_ids if track_id not in existing_track_ids]


def get_missing_artist_ids(db: Session) -> list[str]:
    stream_artist_ids = (
        db.execute(
            select(Stream.artist_id)
            .where(Stream.artist_id.is_not(None), Stream.artist_id != "")
            .distinct()
        )
        .scalars()
        .all()
    )
    if not stream_artist_ids:
        return []

    existing_artist_ids = set(
        db.execute(select(Artist.spotify_id).where(Artist.spotify_id.in_(stream_artist_ids)))
        .scalars()
        .all()
    )
    return [artist_id for artist_id in stream_artist_ids if artist_id not in existing_artist_ids]


def get_track_ids_missing_stream_artist(db: Session) -> list[str]:
    return list(
        set(
            db.execute(
                select(Stream.track_id).where(
                    Stream.track_id.is_not(None),
                    Stream.track_id != "",
                    Stream.artist_id.is_(None) | (Stream.artist_id == ""),
                )
            )
            .scalars()
            .all()
        )
    )


def upsert_tracks_from_spotify(
    db: Session,
    spotify_token: str,
    track_ids: list[str],
    *,
    http_get: HttpGet = httpx.get,
    warn: Warn | None = None,
    report_issue: IssueReporter | None = None,
) -> int:
    """Fetch track metadata in Spotify batches and insert/update tracks."""
    if not track_ids:
        return 0

    headers = {"Authorization": f"Bearer {spotify_token}"}
    upserted = 0

    enriched_track_ids: list[str] = []

    for i in range(0, len(track_ids), BATCH_SIZE):
        batch = track_ids[i : i + BATCH_SIZE]
        batch_track_ids: list[str] = []
        try:
            response = http_get(
                SPOTIFY_TRACKS_URL,
                params={"ids": ",".join(batch)},
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()

            for track_data in response.json().get("tracks", []):
                if not track_data:
                    continue

                spotify_id = track_data.get("id")
                if not spotify_id:
                    continue

                _upsert_track(db, track_data)
                batch_track_ids.append(spotify_id)
                upserted += 1

            db.commit()
            enriched_track_ids.extend(batch_track_ids)
        except Exception as exc:
            issue = spotify_api_issue(
                operation="spotify_track_metadata",
                message=f"spotify track batch fetch failed: {exc}",
                exc=exc,
                identifiers=batch,
            )
            if report_issue:
                report_issue(issue)
            if warn:
                warn(issue.message)
            db.rollback()
            continue

    if enriched_track_ids:
        # Track duration and Stream.artist_id both feed denormalized rollup
        # columns/keys. Recompute once after all successful batches so metadata
        # enrichment cannot leave historical analytics stale. Scoped to the
        # tracks actually enriched — no other stream's rollup contribution can
        # have changed — so cost is O(plays of those tracks), not O(history).
        # The recompute shares a PostgreSQL advisory lock with incremental
        # Stream writes.
        try:
            rebuild_rollups_for_tracks(db, enriched_track_ids)
            db.commit()
        except Exception as exc:
            db.rollback()
            # Per-batch Track/Stream writes above are already committed, so the
            # DB is left with fresh metadata and stale rollups. Say so — the
            # repair is a full rebuild.
            raise RuntimeError(
                "spotify track metadata was committed but the rollup recompute failed; "
                "daily_listening_stats/daily_track_stats are now stale. Rerun "
                "`python -m app.scripts.backfill_rollups --commit` to repair."
            ) from exc

    return upserted


def upsert_artists_from_spotify(
    db: Session,
    spotify_token: str,
    artist_ids: list[str],
    *,
    http_get: HttpGet = httpx.get,
    warn: Warn | None = None,
    report_issue: IssueReporter | None = None,
) -> int:
    """Fetch artist metadata in Spotify batches and insert/update artists."""
    if not artist_ids:
        return 0

    headers = {"Authorization": f"Bearer {spotify_token}"}
    upserted = 0

    for i in range(0, len(artist_ids), BATCH_SIZE):
        batch = artist_ids[i : i + BATCH_SIZE]
        try:
            response = http_get(
                SPOTIFY_ARTISTS_URL,
                params={"ids": ",".join(batch)},
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()

            for artist_data in response.json().get("artists", []):
                if not artist_data:
                    continue

                spotify_id = artist_data.get("id")
                if not spotify_id:
                    continue

                _upsert_artist(db, artist_data)
                upserted += 1

            db.commit()
        except Exception as exc:
            issue = spotify_api_issue(
                operation="spotify_artist_metadata",
                message=f"spotify artist batch fetch failed: {exc}",
                exc=exc,
                identifiers=batch,
            )
            if report_issue:
                report_issue(issue)
            if warn:
                warn(issue.message)
            db.rollback()
            continue

    return upserted


def _upsert_track(db: Session, track_data: dict[str, Any]) -> None:
    spotify_id = track_data["id"]
    album = track_data.get("album", {}) or {}
    album_images = album.get("images") or []
    cover = album_images[0]["url"] if album_images else None
    artists = track_data.get("artists") or []
    artist_name = artists[0]["name"] if artists else None
    artist_id = artists[0]["id"] if artists else None

    existing = db.execute(select(Track).where(Track.spotify_id == spotify_id)).scalar_one_or_none()
    if existing:
        existing.name = track_data.get("name") or existing.name
        existing.artist_name = artist_name or existing.artist_name
        existing.album_name = album.get("name") or existing.album_name
        existing.album_image = cover or existing.album_image
        existing.duration_ms = track_data.get("duration_ms") or existing.duration_ms
        existing.popularity = track_data.get("popularity") or existing.popularity
    else:
        db.add(
            Track(
                spotify_id=spotify_id,
                name=track_data.get("name") or "",
                artist_name=artist_name,
                album_name=album.get("name"),
                album_image=cover,
                duration_ms=track_data.get("duration_ms"),
                popularity=track_data.get("popularity"),
            )
        )

    if artist_id:
        db.execute(
            update(Stream)
            .where(
                Stream.track_id == spotify_id,
                Stream.artist_id.is_(None) | (Stream.artist_id == ""),
            )
            .values(artist_id=artist_id)
        )


def _upsert_artist(db: Session, artist_data: dict[str, Any]) -> None:
    spotify_id = artist_data["id"]
    name = artist_data.get("name")
    genres = artist_data.get("genres") or []
    popularity = artist_data.get("popularity")
    images = artist_data.get("images") or []
    image_url = images[0]["url"] if images else None

    existing = db.execute(
        select(Artist).where(Artist.spotify_id == spotify_id)
    ).scalar_one_or_none()
    if existing:
        existing.name = name or existing.name
        existing.genres = genres or existing.genres
        existing.popularity = popularity or existing.popularity
        existing.image_url = image_url or existing.image_url
    else:
        db.add(
            Artist(
                spotify_id=spotify_id,
                name=name or "",
                genres=genres,
                popularity=popularity,
                image_url=image_url,
            )
        )
