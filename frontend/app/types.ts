export interface Song {
  track_name: string;
  artist_name: string;
  track_id: string;
  artist_id: string | null;
  album_image: string | null;
  play_count: number;
}

export interface Artist {
  artist_name: string;
  spotify_id: string | null;
  image_url: string | null;
  play_count: number;
}

export interface Album {
  album_name: string;
  artist_name: string;
  artist_id: string | null;
  album_image: string | null;
  play_count: number;
}

export interface AvailablePeriods {
  years: number[];
  earliest_date: string | null;
  latest_date: string | null;
}

export interface ListeningStats {
  total_play_count: number;
  total_listening_ms: number;
  unique_songs: number;
  unique_artists: number;
  unique_albums: number;
  active_days: number;
}

export interface RecentlyPlayedItem {
  track_name: string;
  artist_name: string;
  track_id: string | null;
  artist_id: string | null;
  album_image: string | null;
  played_at: string;
}

export interface RecentlyPlayedResponse {
  items: RecentlyPlayedItem[];
  page: number;
  page_size: number;
  has_next: boolean;
}

/** Union used by MediaSection / MediaCard */
export type MediaItem = Song | Artist | Album;

// --- Detail page types ---

export interface PlayCountOverTime {
  period: string;
  play_count: number;
}

export interface ArtistDetail {
  spotify_id: string;
  name: string;
  image_url: string | null;
  genres: string[];
  popularity: number | null;
  total_play_count: number;
}

export interface ArtistDetailResponse {
  artist: ArtistDetail;
  top_songs: Song[];
  top_albums: Album[];
  timeline: PlayCountOverTime[];
  granularity: string;
  available_periods: AvailablePeriods;
}

export interface AlbumDetail {
  album_name: string;
  artist_name: string;
  artist_id: string | null;
  album_image: string | null;
  total_play_count: number;
  unique_tracks_played: number;
}

export interface AlbumDetailResponse {
  album: AlbumDetail;
  top_tracks: Song[];
  timeline: PlayCountOverTime[];
  granularity: string;
  available_periods: AvailablePeriods;
}

export interface TrackDetail {
  spotify_id: string | null;
  name: string;
  artist_name: string;
  artist_id: string | null;
  album_name: string | null;
  album_image: string | null;
  duration_ms: number | null;
  total_play_count: number;
}

export interface TrackDetailResponse {
  track: TrackDetail;
  timeline: PlayCountOverTime[];
  granularity: string;
  available_periods: AvailablePeriods;
}

export interface RelatedTrack {
  track_id: string;
  track_name: string;
  artist_id: string | null;
  artist_name: string;
  album_image: string | null;
  cooc_sessions: number;
  /** Fraction of target's sessions that also contained this track (0-1). */
  cooc_rate: number;
  score: number;
}

export interface RelatedTracksResponse {
  related: RelatedTrack[];
}

export interface TimelineResponse {
  timeline: PlayCountOverTime[];
  granularity: string;
}
