'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useEffect, useState } from 'react';
import useSWR from 'swr';
import { localFetcher } from '@/app/lib/fetcher';

interface CurrentlyPlayingData {
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

interface CurrentlyPlayingProps {
  timeframe?: string;
  date?: string | null;
}

function formatTime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

function buildQuery(timeframe?: string, date?: string | null): string {
  if (!timeframe) return '';
  const params = new URLSearchParams({ timeframe });
  if (date) params.set('date', date);
  return `?${params.toString()}`;
}

function buildSongHref(
  data: CurrentlyPlayingData,
  query: string,
): string {
  const id = data.track_id
    ? data.track_id
    : encodeURIComponent(`${data.track_name}--${data.artist_name}`);
  return `/songs/${id}${query}`;
}

function buildArtistHref(data: CurrentlyPlayingData, query: string): string | null {
  if (!data.artist_id) return null;
  return `/artists/${data.artist_id}${query}`;
}

function buildAlbumHref(data: CurrentlyPlayingData, query: string): string | null {
  if (!data.artist_id || !data.album_name) return null;
  return `/albums/${encodeURIComponent(`${data.artist_id}--${data.album_name}`)}${query}`;
}

export default function CurrentlyPlaying({
  timeframe,
  date,
}: CurrentlyPlayingProps) {
  const { data: currentlyPlaying, mutate } = useSWR<CurrentlyPlayingData>(
    '/api/currently-playing',
    localFetcher,
    {
      refreshInterval: 15_000,
      revalidateOnFocus: true,
      revalidateIfStale: true,
      dedupingInterval: 10_000,
      errorRetryCount: 3,
    },
  );

  const serverProgressMs = currentlyPlaying?.progress_ms;
  const trackName = currentlyPlaying?.track_name;
  const [progressMs, setProgressMs] = useState(serverProgressMs ?? 0);
  const [synced, setSynced] = useState({ serverProgressMs, trackName });

  // Snap the local progress to the server's whenever a revalidation reports a
  // new position or track.
  if (synced.serverProgressMs !== serverProgressMs || synced.trackName !== trackName) {
    setSynced({ serverProgressMs, trackName });
    if (serverProgressMs != null) setProgressMs(serverProgressMs);
  }

  useEffect(() => {
    if (!currentlyPlaying?.is_playing || currentlyPlaying.duration_ms == null) {
      return;
    }
    const duration = currentlyPlaying.duration_ms;
    const interval = setInterval(() => {
      setProgressMs((prev) => Math.min(prev + 1000, duration));
    }, 1000);
    return () => clearInterval(interval);
  }, [currentlyPlaying?.is_playing, currentlyPlaying?.duration_ms, currentlyPlaying?.track_name]);

  // Re-poll right as the track is expected to end, instead of leaving the
  // bar pinned at full until the next scheduled 15s tick. Anchored to the
  // server-reported progress_ms (not the locally-ticked one) so it
  // re-derives cleanly on every revalidation instead of accumulating drift.
  useEffect(() => {
    if (
      !currentlyPlaying?.is_playing ||
      currentlyPlaying.progress_ms == null ||
      currentlyPlaying.duration_ms == null
    ) {
      return;
    }
    const remainingMs = currentlyPlaying.duration_ms - currentlyPlaying.progress_ms;
    // Small buffer past the deadline so Spotify's own playback state has
    // actually advanced to the next track by the time we ask.
    const timeout = setTimeout(() => {
      mutate();
    }, Math.max(remainingMs, 0) + 500);
    return () => clearTimeout(timeout);
  }, [
    currentlyPlaying?.is_playing,
    currentlyPlaying?.progress_ms,
    currentlyPlaying?.duration_ms,
    currentlyPlaying?.track_name,
    mutate,
  ]);

  if (!currentlyPlaying?.is_playing) {
    return <div className="flex items-center" />;
  }

  const query = buildQuery(timeframe, date);
  const songHref = buildSongHref(currentlyPlaying, query);
  const artistHref = buildArtistHref(currentlyPlaying, query);
  const albumHref = buildAlbumHref(currentlyPlaying, query);
  const imageHref = albumHref ?? songHref;

  const duration = currentlyPlaying.duration_ms ?? 0;
  const percentage = duration > 0 ? (progressMs / duration) * 100 : 0;

  return (
    <div className="flex items-center">
      <div className="flex items-center gap-2 sm:gap-4 w-full sm:w-auto">
        <div className="flex-1 sm:flex-none sm:text-right h-16 sm:h-20 flex flex-col justify-between">
          <div className="text-xs text-spotify font-lexend">
            NOW PLAYING
          </div>
          <Link
            href={songHref}
            className="font-lexend font-normal text-sm sm:text-lg truncate hover:underline"
          >
            {currentlyPlaying.track_name}
          </Link>
          {artistHref ? (
            <Link
              href={artistHref}
              className="font-lexend font-light text-xs sm:text-sm text-muted truncate hover:text-foreground hover:underline transition-colors"
            >
              {currentlyPlaying.artist_name}
            </Link>
          ) : (
            <div className="font-lexend font-light text-xs sm:text-sm text-muted truncate">
              {currentlyPlaying.artist_name}
            </div>
          )}
          <div className="flex items-center gap-2">
            <span className="text-micro font-lexend text-muted tabular-nums">
              {formatTime(progressMs)}
            </span>
            <div className="flex-1 h-1 rounded-full bg-subtle min-w-progress">
              <div
                className="h-full rounded-full bg-spotify transition-all duration-1000 linear"
                style={{ width: `${Math.min(percentage, 100)}%` }}
              />
            </div>
            <span className="text-micro font-lexend text-muted tabular-nums">
              {formatTime(duration)}
            </span>
          </div>
        </div>
        {currentlyPlaying.album_image && (
          <Link
            href={imageHref}
            aria-label="Currently playing"
            className="shrink-0 hover:opacity-80 transition-opacity"
          >
            <Image
              src={currentlyPlaying.album_image}
              alt="Currently playing"
              width={80}
              height={80}
              className="w-16 h-16 sm:w-20 sm:h-20 rounded-md"
            />
          </Link>
        )}
      </div>
    </div>
  );
}
