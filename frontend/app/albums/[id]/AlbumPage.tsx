'use client';

import { useState, useMemo } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import useSWR from 'swr';
import Image from 'next/image';
import Link from 'next/link';
import Breadcrumb from '@/app/components/Breadcrumb';
import TimelineSelector from '@/app/components/TimelineSelector';
import ListeningTimeline from '@/app/components/ListeningTimeline';
import MediaSection from '@/app/components/MediaSection';
import { fetcher } from '@/app/lib/fetcher';
import { pluralize, timeframeQuery } from '@/app/lib/timeframe';
import type { AlbumDetailResponse } from '@/app/types';

interface AlbumPageProps {
  artistId: string;
  albumName: string;
  initialData: AlbumDetailResponse;
  initialTimeframe: string;
  initialDate: string | null;
}

export default function AlbumPage({
  artistId,
  albumName,
  initialData,
  initialTimeframe,
  initialDate,
}: AlbumPageProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [currentTimeframe, setCurrentTimeframe] = useState(initialTimeframe);
  const [currentDate, setCurrentDate] = useState<string | null>(initialDate);

  const query = useMemo(
    () => timeframeQuery(currentTimeframe, currentDate),
    [currentTimeframe, currentDate],
  );
  const swrKey = useMemo(() => {
    const p = new URLSearchParams({
      artist_id: artistId,
      album_name: albumName,
      timeframe: currentTimeframe,
    });
    if (currentDate) p.set('date', currentDate);
    return `/api/albums/by-name?${p.toString()}`;
  }, [artistId, albumName, currentTimeframe, currentDate]);
  const initialKey = useMemo(() => {
    const p = new URLSearchParams({
      artist_id: artistId,
      album_name: albumName,
      timeframe: initialTimeframe,
    });
    if (initialDate) p.set('date', initialDate);
    return `/api/albums/by-name?${p.toString()}`;
  }, [artistId, albumName, initialTimeframe, initialDate]);
  const isInitialKey = swrKey === initialKey;

  const { data, error, isLoading } = useSWR<AlbumDetailResponse>(
    swrKey,
    fetcher,
    {
      fallbackData: isInitialKey ? initialData : undefined,
      keepPreviousData: true,
      revalidateIfStale: !isInitialKey,
      revalidateOnFocus: false,
    },
  );

  const handleTimeframeChange = (timeframe: string, date: string | null) => {
    setCurrentTimeframe(timeframe);
    setCurrentDate(date);
    router.replace(`${pathname}?${timeframeQuery(timeframe, date)}`, { scroll: false });
  };

  const homeHref = useMemo(() => `/?${query}`, [query]);

  const album = data?.album ?? initialData.album;
  const topTracks = data?.top_tracks ?? [];
  const timeline = data?.timeline ?? initialData.timeline;
  const granularity = data?.granularity ?? initialData.granularity;
  const periods = data?.available_periods ?? initialData.available_periods;

  return (
    <>
      <Breadcrumb items={[{ label: 'Home', href: homeHref }, { label: album.album_name }]} />

      {/* Album header */}
      <div className="flex flex-col sm:flex-row items-start gap-6 mb-8">
        {album.album_image && (
          <Image
            src={album.album_image}
            alt={album.album_name}
            width={200}
            height={200}
            className="w-40 h-40 sm:w-48 sm:h-48 object-cover rounded-md"
          />
        )}
        <div className="flex flex-col justify-center">
          <h1 className="font-tinos text-3xl sm:text-4xl md:text-5xl font-normal mb-3">
            {album.album_name}
          </h1>
          <div className="font-lexend font-light text-base text-muted mb-1">
            {album.artist_id ? (
              <Link
                href={`/artists/${album.artist_id}?${query}`}
                className="hover:text-foreground transition-colors"
              >
                {album.artist_name}
              </Link>
            ) : (
              album.artist_name
            )}
          </div>
          <div className="font-lexend font-light text-sm text-muted mb-2">
            {album.unique_tracks_played.toLocaleString()} {pluralize(album.unique_tracks_played, 'track')} played
          </div>
          <div className="font-lexend font-light text-lg text-foreground">
            {album.total_play_count.toLocaleString()} {pluralize(album.total_play_count, 'play')}
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

          <MediaSection
            title="Top Tracks"
            items={topTracks}
            type="songs"
            timeframe={currentTimeframe}
            date={currentDate}
          />
        </div>
      )}
    </>
  );
}
