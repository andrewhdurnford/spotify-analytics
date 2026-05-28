'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import useSWR from 'swr';
import { fetcher } from '@/app/lib/fetcher';

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

function buildSongHref(
  data: CurrentlyPlayingData,
  timeframe?: string,
  date?: string | null,
): string {
  let id: string;
  if (data.track_id) {
    id = data.track_id;
  } else {
    id = encodeURIComponent(`${data.track_name}--${data.artist_name}`);
  }

  let query = '';
  if (timeframe) {
    const params = new URLSearchParams({ timeframe });
    if (date) params.set('date', date);
    query = `?${params.toString()}`;
  }

  return `/songs/${id}${query}`;
}

export default function CurrentlyPlaying({
  timeframe,
  date,
}: CurrentlyPlayingProps) {
  const { data: currentlyPlaying } = useSWR<CurrentlyPlayingData>(
    '/api/top/currently-playing',
    fetcher,
    {
      refreshInterval: 15_000,
      revalidateOnFocus: true,
      revalidateIfStale: true,
      dedupingInterval: 10_000,
      errorRetryCount: 3,
    },
  );

  const [progressMs, setProgressMs] = useState(0);
  const lastSyncRef = useRef<number>(Date.now());

  // Sync local progress when API data arrives
  useEffect(() => {
    if (currentlyPlaying?.progress_ms != null) {
      setProgressMs(currentlyPlaying.progress_ms);
      lastSyncRef.current = Date.now();
    }
  }, [currentlyPlaying?.progress_ms, currentlyPlaying?.track_name]);

  // Interpolate progress client-side between API polls
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

  const duration = currentlyPlaying?.duration_ms ?? 0;
  const percentage = duration > 0 ? (progressMs / duration) * 100 : 0;

  return (
    <div className="flex items-center">
      {currentlyPlaying?.is_playing && (
        <Link
          href={buildSongHref(currentlyPlaying, timeframe, date)}
          className="flex items-center gap-2 sm:gap-4 w-full sm:w-auto hover:opacity-80 transition-opacity"
        >
          <div className="flex-1 sm:flex-none sm:text-right h-16 sm:h-20 flex flex-col justify-between">
            <div className="text-xs text-spotify font-lexend">
              NOW PLAYING
            </div>
            <div className="font-lexend font-normal text-sm sm:text-lg truncate">
              {currentlyPlaying.track_name}
            </div>
            <div className="font-lexend font-light text-xs sm:text-sm text-muted truncate">
              {currentlyPlaying.artist_name}
            </div>
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
            <Image
              src={currentlyPlaying.album_image}
              alt="Currently playing"
              width={80}
              height={80}
              className="w-16 h-16 sm:w-20 sm:h-20 rounded-md shrink-0"
            />
          )}
        </Link>
      )}
    </div>
  );
}
