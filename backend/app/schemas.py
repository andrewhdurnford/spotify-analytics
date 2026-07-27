"""Pydantic response models for the API."""

from pydantic import BaseModel

# --- Top items ---


class TopSong(BaseModel):
    track_name: str
    artist_name: str
    track_id: str | None = None
    artist_id: str | None = None
    album_image: str | None = None
    play_count: int


class TopArtist(BaseModel):
    artist_name: str
    spotify_id: str | None = None
    image_url: str | None = None
    play_count: int


class TopAlbum(BaseModel):
    album_name: str | None = None
    artist_name: str
    artist_id: str | None = None
    album_image: str | None = None
    play_count: int


class ListeningStats(BaseModel):
    total_play_count: int
    total_listening_ms: int
    unique_songs: int
    unique_artists: int
    unique_albums: int
    active_days: int


class AvailablePeriods(BaseModel):
    years: list[int]
    earliest_date: str | None = None
    latest_date: str | None = None


class RecentlyPlayedItem(BaseModel):
    track_name: str
    artist_name: str
    track_id: str | None = None
    artist_id: str | None = None
    album_image: str | None = None
    played_at: str


class RecentlyPlayedResponse(BaseModel):
    items: list[RecentlyPlayedItem]
    page: int
    page_size: int
    has_next: bool


# --- Currently playing ---


class CurrentlyPlaying(BaseModel):
    is_playing: bool
    track_id: str | None = None
    track_name: str | None = None
    artist_id: str | None = None
    artist_name: str | None = None
    album_name: str | None = None
    album_image: str | None = None
    progress_ms: int | None = None
    duration_ms: int | None = None


# --- Generic responses ---


class MessageResponse(BaseModel):
    message: str


class HealthResponse(BaseModel):
    status: str
    message: str


class SyncStatusResponse(BaseModel):
    status: str
    interval: str


class CronSyncResponse(BaseModel):
    status: str
    message: str


# --- Error responses ---


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


# --- Detail pages ---


class PlayCountOverTime(BaseModel):
    period: str
    play_count: int


class ArtistDetail(BaseModel):
    spotify_id: str
    name: str
    image_url: str | None = None
    genres: list[str] = []
    popularity: int | None = None
    total_play_count: int


class ArtistDetailResponse(BaseModel):
    artist: ArtistDetail
    top_songs: list[TopSong]
    top_albums: list[TopAlbum]
    timeline: list[PlayCountOverTime]
    granularity: str  # "monthly" or "daily"
    available_periods: AvailablePeriods


class AlbumDetail(BaseModel):
    album_name: str
    artist_name: str
    artist_id: str | None = None
    album_image: str | None = None
    total_play_count: int
    unique_tracks_played: int


class AlbumDetailResponse(BaseModel):
    album: AlbumDetail
    top_tracks: list[TopSong]
    timeline: list[PlayCountOverTime]
    granularity: str  # "monthly" or "daily"
    available_periods: AvailablePeriods


class TrackDetail(BaseModel):
    spotify_id: str | None = None
    name: str
    artist_name: str
    artist_id: str | None = None
    album_name: str | None = None
    album_image: str | None = None
    duration_ms: int | None = None
    total_play_count: int


class TrackDetailResponse(BaseModel):
    track: TrackDetail
    timeline: list[PlayCountOverTime]
    granularity: str  # "monthly" or "daily"
    available_periods: AvailablePeriods


class RelatedTrack(BaseModel):
    track_id: str
    track_name: str
    artist_id: str | None = None
    artist_name: str
    album_image: str | None = None
    cooc_sessions: int
    # Fraction of the target track's sessions that also contained this track.
    # Intuitive display value: "X% of your [target] sessions".
    cooc_rate: float
    # Cosine similarity over session-membership. Used for ranking.
    score: float


class RelatedTracksResponse(BaseModel):
    related: list[RelatedTrack]


class TimelineResponse(BaseModel):
    timeline: list[PlayCountOverTime]
    granularity: str  # "monthly" or "daily"
