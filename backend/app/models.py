from datetime import date as date_type
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .database import Base


class Track(Base):
    __tablename__ = "tracks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    spotify_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    artist_name: Mapped[str | None] = mapped_column(String(255), default=None)
    album_name: Mapped[str | None] = mapped_column(String(512), default=None)
    album_image: Mapped[str | None] = mapped_column(String(1000), default=None)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    popularity: Mapped[int | None] = mapped_column(Integer, default=None)
    first_played_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_updated: Mapped[datetime | None] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="track", foreign_keys="Stream.track_id"
    )


class Artist(Base):
    __tablename__ = "artists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    spotify_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    genres: Mapped[list] = mapped_column(JSON, default=list)
    popularity: Mapped[int | None] = mapped_column(Integer, default=None)
    image_url: Mapped[str | None] = mapped_column(String(1000), default=None)

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="artist", foreign_keys="Stream.artist_id"
    )


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    track_name: Mapped[str] = mapped_column(String(255), index=True)
    artist_name: Mapped[str] = mapped_column(String(255), index=True)
    album_name: Mapped[str | None] = mapped_column(String(255), default=None)
    played_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    track_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("tracks.spotify_id"), default=None
    )
    artist_id: Mapped[str | None] = mapped_column(
        String(100), ForeignKey("artists.spotify_id"), default=None
    )

    track: Mapped[Optional["Track"]] = relationship(
        back_populates="streams", foreign_keys=[track_id]
    )
    artist: Mapped[Optional["Artist"]] = relationship(
        back_populates="streams", foreign_keys=[artist_id]
    )

    __table_args__ = (
        UniqueConstraint("track_id", "played_at", name="uix_trackid_playedat"),
        UniqueConstraint("track_name", "artist_name", "played_at", name="uix_name_artist_playedat"),
        Index("ix_streams_played_at_track_id", "played_at", "track_id"),
        Index("ix_streams_played_at_artist_id", "played_at", "artist_id"),
    )


class DailyListeningStats(Base):
    """Rollup: one row per Toronto-local calendar date across all streams.

    Incrementally maintained (see app.services.rollups) whenever a Stream row
    is inserted, and rebuildable from scratch via
    app.services.rollups.rebuild_all_rollups.
    """

    __tablename__ = "daily_listening_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[date_type] = mapped_column(Date, unique=True, index=True)
    play_count: Mapped[int] = mapped_column(Integer, default=0)
    # Sum of Track.duration_ms across plays that day. Mirrors the historical
    # get_listening_stats semantics: only plays whose track_id resolves to a
    # known Track row with a duration contribute; unresolved plays add 0 (no
    # fallback to Stream.duration_ms).
    listening_ms: Mapped[int] = mapped_column(Integer, default=0)


class DailyTrackStats(Base):
    """Rollup: one row per (Toronto-local date, track/artist/album) grouping.

    Grain matches what a single Stream row would coalesce to for grouping
    purposes (track_id-or-name, artist_id-or-name, album_name-or-empty), one
    row per calendar day. Used to derive top songs/artists/albums, unique
    counts, and per-entity timelines without scanning raw `streams`.
    """

    __tablename__ = "daily_track_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    date: Mapped[date_type] = mapped_column(Date, index=True)

    # Grouping keys: coalesce(id, name) for track/artist; album_name or ""
    # sentinel for "no album" (keeps the composite unique constraint simple —
    # NULLs aren't equal to each other in SQL, so a sentinel avoids duplicate
    # "no album" rows per day/track/artist).
    track_key: Mapped[str] = mapped_column(String(600), index=True)
    artist_key: Mapped[str] = mapped_column(String(300), index=True)
    album_key: Mapped[str] = mapped_column(String(600), default="")

    # Display / exact-filter columns, last-seen-wins on incremental updates.
    track_id: Mapped[str | None] = mapped_column(String(100), default=None, index=True)
    artist_id: Mapped[str | None] = mapped_column(String(100), default=None, index=True)
    track_name: Mapped[str] = mapped_column(String(255))
    artist_name: Mapped[str] = mapped_column(String(255))
    album_name: Mapped[str | None] = mapped_column(String(255), default=None)

    play_count: Mapped[int] = mapped_column(Integer, default=0)
    listening_ms: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        UniqueConstraint(
            "date", "track_key", "artist_key", "album_key", name="uix_daily_track_stats_key"
        ),
        Index("ix_daily_track_stats_date_track_key", "date", "track_key"),
        Index("ix_daily_track_stats_date_artist_key", "date", "artist_key"),
        Index("ix_daily_track_stats_date_album", "date", "album_key", "artist_name"),
    )


@event.listens_for(Session, "before_flush")
def _maintain_daily_rollups(session: Session, flush_context, instances) -> None:
    """Keep daily_listening_stats/daily_track_stats in sync with new Stream rows.

    Registered here (rather than in app.services.rollups) so it fires for
    every sessionmaker-derived Session regardless of call site — playback
    sync, historical import scripts, and tests that seed Stream rows
    directly all get rollups maintained transactionally, for free. The
    actual update logic lives in app.services.rollups; imported lazily here
    to avoid a models<->services import cycle.
    """
    if not any(isinstance(obj, Stream) for obj in session.new):
        return

    from .services.rollups import maintain_rollups_before_flush

    maintain_rollups_before_flush(session)
