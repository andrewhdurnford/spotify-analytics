'use client';

import { useState, useMemo } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import useSWR from 'swr';
import Image from 'next/image';
import Link from 'next/link';
import Breadcrumb from '@/app/components/Breadcrumb';
import TimelineSelector from '@/app/components/TimelineSelector';
import ListeningTimeline from '@/app/components/ListeningTimeline';
import RelatedTracks from '@/app/components/RelatedTracks';
import { fetcher } from '@/app/lib/fetcher';
import { pluralize, timeframeQuery } from '@/app/lib/timeframe';
import type { TrackDetailResponse } from '@/app/types';

interface SongPageProps {
  songId: string;
  isNameFallback: boolean;
  initialData: TrackDetailResponse;
  initialTimeframe: string;
  initialDate: string | null;
}

function formatDuration(ms: number): string {
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

export default function SongPage({ songId, isNameFallback, initialData, initialTimeframe, initialDate }: SongPageProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [currentTimeframe, setCurrentTimeframe] = useState(initialTimeframe);
  const [currentDate, setCurrentDate] = useState<string | null>(initialDate);

  const query = useMemo(
    () => timeframeQuery(currentTimeframe, currentDate),
    [currentTimeframe, currentDate],
  );

  const swrKey = useMemo(() => {
    if (isNameFallback) {
      const [trackName, artistName] = decodeURIComponent(songId).split('--');
      const p = new URLSearchParams({ timeframe: currentTimeframe, track_name: trackName, artist_name: artistName });
      if (currentDate) p.set('date', currentDate);
      return `/api/tracks/by-name?${p.toString()}`;
    }
    return `/api/tracks/${songId}?${query}`;
  }, [songId, isNameFallback, currentTimeframe, currentDate, query]);

  const { data, error, isLoading } = useSWR<TrackDetailResponse>(
    swrKey,
    fetcher,
    { fallbackData: initialData },
  );

  const handleTimeframeChange = (timeframe: string, date: string | null) => {
    setCurrentTimeframe(timeframe);
    setCurrentDate(date);
    router.replace(`${pathname}?${timeframeQuery(timeframe, date)}`, { scroll: false });
  };

  const homeHref = useMemo(() => `/?${query}`, [query]);

  const track = data?.track ?? initialData.track;
  const timeline = data?.timeline ?? initialData.timeline;
  const granularity = data?.granularity ?? initialData.granularity;
  const periods = data?.available_periods ?? initialData.available_periods;

  return (
    <>
      <Breadcrumb items={[{ label: 'Home', href: homeHref }, { label: track.name }]} />

      {/* Track header */}
      <div className="flex flex-col sm:flex-row items-start gap-6 mb-8">
        {track.album_image && (
          <Image
            src={track.album_image}
            alt={track.name}
            width={200}
            height={200}
            className="w-40 h-40 sm:w-48 sm:h-48 object-cover rounded-md"
          />
        )}
        <div className="flex flex-col justify-center">
          <h1 className="font-tinos text-3xl sm:text-4xl md:text-5xl font-normal mb-3">
            {track.name}
          </h1>
          <div className="font-lexend font-light text-base text-muted mb-1">
            {track.artist_id ? (
              <Link
                href={`/artists/${track.artist_id}?${query}`}
                className="hover:text-foreground transition-colors"
              >
                {track.artist_name}
              </Link>
            ) : (
              track.artist_name
            )}
          </div>
          {track.album_name && (
            <div className="font-lexend font-light text-sm text-muted mb-2">
              {track.artist_id ? (
                <Link
                  href={`/albums/${encodeURIComponent(`${track.artist_id}--${track.album_name}`)}?${query}`}
                  className="hover:text-foreground transition-colors"
                >
                  {track.album_name}
                </Link>
              ) : (
                track.album_name
              )}
            </div>
          )}
          {track.duration_ms && (
            <div className="font-lexend font-light text-sm text-muted mb-2">
              {formatDuration(track.duration_ms)}
            </div>
          )}
          <div className="font-lexend font-light text-lg text-foreground">
            {track.total_play_count.toLocaleString()} {pluralize(track.total_play_count, 'play')}
          </div>
        </div>
      </div>

      <TimelineSelector
        onTimeframeChange={handleTimeframeChange}
        availableYears={periods.years}
        earliestDate={periods.earliest_date}
        latestDate={periods.latest_date}
        selectedTimeframe={currentTimeframe}
        selectedDate={currentDate}
      />

      {error ? (
        <div className="text-center py-12">
          <div className="font-lexend font-light text-danger text-lg">
            Failed to load data. Please try again later.
          </div>
        </div>
      ) : isLoading ? (
        <div className="text-center py-12">
          <div className="text-lg sm:text-xl font-lexend font-light">Loading...</div>
        </div>
      ) : (
        <div className="space-y-6 sm:space-y-8">
          <div>
            <h2 className="font-lexend text-xl sm:text-2xl font-normal mb-4">
              Listening Over Time
            </h2>
            <ListeningTimeline data={timeline} granularity={granularity} />
          </div>

          {track.spotify_id && (
            <RelatedTracks trackId={track.spotify_id} artistId={track.artist_id} />
          )}
        </div>
      )}
    </>
  );
}
