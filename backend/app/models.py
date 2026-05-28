from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
