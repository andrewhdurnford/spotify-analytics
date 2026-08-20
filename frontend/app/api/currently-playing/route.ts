import { NextResponse } from 'next/server';

// This route talks to Spotify directly instead of proxying through the
// backend -- it's a pure read with no DB write, so it doesn't need to share
// Render's request budget, and the frontend polls it every 15s.
export const dynamic = 'force-dynamic';

const SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token';
const SPOTIFY_CURRENTLY_PLAYING_URL = 'https://api.spotify.com/v1/me/player/currently-playing';

// Never cached: the completion-triggered repoll in CurrentlyPlaying.tsx
// needs the immediate call after a track ends to be genuinely fresh, not
// served from an edge cache entry set moments earlier.
const CACHE_CONTROL = 'no-store';

interface CurrentlyPlayingResponse {
  is_playing: boolean;
  track_id?: string;
  track_name?: string;
  artist_id?: string;
  artist_name?: string;
  album_name?: string;
  album_image?: string;
  progress_ms?: number;
  duration_ms?: number;
}

const NOT_PLAYING: CurrentlyPlayingResponse = { is_playing: false };

// Module-scope so a warm serverless instance reuses the token instead of
// refreshing on every request. Mirrors backend/app/dependencies.py's
// get_spotify_client, which makes the same tradeoff for the same reason.
let accessToken: string | null = null;
let tokenExpiresAt = 0;

async function getAccessToken(): Promise<string> {
  if (accessToken && Date.now() < tokenExpiresAt) {
    return accessToken;
  }

  const clientId = process.env.SPOTIFY_CLIENT_ID;
  const clientSecret = process.env.SPOTIFY_CLIENT_SECRET;
  const refreshToken = process.env.SPOTIFY_REFRESH_TOKEN;
  if (!clientId || !clientSecret || !refreshToken) {
    throw new Error('Spotify credentials are not configured');
  }

  const res = await fetch(SPOTIFY_TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'refresh_token',
      refresh_token: refreshToken,
      client_id: clientId,
      client_secret: clientSecret,
    }),
    cache: 'no-store',
  });

  if (!res.ok) {
    throw new Error(`Spotify token refresh failed (${res.status})`);
  }

  const data = await res.json();
  accessToken = data.access_token as string;
  tokenExpiresAt = Date.now() + data.expires_in * 1000;
  return accessToken;
}

function formatCurrentlyPlaying(data: {
  is_playing?: boolean;
  progress_ms?: number;
  item?: {
    id?: string;
    name?: string;
    type?: string;
    duration_ms?: number;
    artists?: { id?: string; name?: string }[];
    album?: { name?: string; images?: { url?: string }[] };
  };
}): CurrentlyPlayingResponse {
  const item = data.item;
  if (!data.is_playing || !item || (item.type && item.type !== 'track')) {
    return NOT_PLAYING;
  }

  const artists = item.artists ?? [];
  return {
    is_playing: true,
    track_id: item.id,
    track_name: item.name,
    artist_id: artists[0]?.id,
    artist_name: artists.map((artist) => artist.name).join(', '),
    album_name: item.album?.name,
    album_image: item.album?.images?.[0]?.url,
    progress_ms: data.progress_ms,
    duration_ms: item.duration_ms,
  };
}

export async function GET() {
  try {
    const token = await getAccessToken();
    const res = await fetch(SPOTIFY_CURRENTLY_PLAYING_URL, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    });

    if (res.status === 204) {
      return NextResponse.json(NOT_PLAYING, { headers: { 'Cache-Control': CACHE_CONTROL } });
    }

    if (res.status === 401) {
      // Token expired mid-flight (clock skew, revocation) -- drop it so the
      // next request refreshes instead of retrying with a dead token.
      accessToken = null;
    }

    if (!res.ok) {
      return NextResponse.json(NOT_PLAYING, { headers: { 'Cache-Control': CACHE_CONTROL } });
    }

    const data = await res.json();
    return NextResponse.json(formatCurrentlyPlaying(data), {
      headers: { 'Cache-Control': CACHE_CONTROL },
    });
  } catch {
    return NextResponse.json(NOT_PLAYING, { headers: { 'Cache-Control': CACHE_CONTROL } });
  }
}
