'use client';

import { useState, useMemo } from 'react';
import useSWR from 'swr';
import Image from 'next/image';
import Breadcrumb from '@/app/components/Breadcrumb';
import TimelineSelector from '@/app/components/TimelineSelector';
import ListeningTimeline from '@/app/components/ListeningTimeline';
import MediaSection from '@/app/components/MediaSection';
import { fetcher } from '@/app/lib/fetcher';
import type { ArtistDetailResponse, AvailablePeriods } from '@/app/types';

interface ArtistPageProps {
  artistId: string;
  initialData: ArtistDetailResponse;
  initialPeriods: AvailablePeriods;
  initialTimeframe: string;
  initialDate: string | null;
}

export default function ArtistPage({ artistId, initialData, initialPeriods, initialTimeframe, initialDate }: ArtistPageProps) {
  const [currentTimeframe, setCurrentTimeframe] = useState(initialTimeframe);
  const [currentDate, setCurrentDate] = useState<string | null>(initialDate);

  const params = useMemo(() => {
    const p = new URLSearchParams({ timeframe: currentTimeframe });
    if (currentDate) p.set('date', currentDate);
    return p.toString();
  }, [currentTimeframe, currentDate]);

  const { data, error, isLoading } = useSWR<ArtistDetailResponse>(
    `/api/artists/${artistId}?${params}`,
    fetcher,
    { fallbackData: initialData },
  );

  const handleTimeframeChange = (timeframe: string, date: string | null) => {
    setCurrentTimeframe(timeframe);
    setCurrentDate(date);
  };

  const homeHref = useMemo(() => {
    const p = new URLSearchParams({ timeframe: currentTimeframe });
    if (currentDate) p.set('date', currentDate);
    return `/?${p.toString()}`;
  }, [currentTimeframe, currentDate]);

  const artist = data?.artist ?? initialData.artist;
  const topSongs = data?.top_songs ?? [];
  const topAlbums = data?.top_albums ?? [];
  const timeline = data?.timeline ?? initialData.timeline;
  const granularity = data?.granularity ?? initialData.granularity;

  return (
    <>
      <Breadcrumb items={[{ label: 'Home', href: homeHref }, { label: artist.name }]} />

      {/* Artist header */}
      <div className="flex flex-col sm:flex-row items-start gap-6 mb-8">
        {artist.image_url && (
          <Image
            src={artist.image_url}
            alt={artist.name}
            width={200}
            height={200}
            className="w-40 h-40 sm:w-48 sm:h-48 object-cover rounded-full"
          />
        )}
        <div className="flex flex-col justify-center">
          <h1 className="font-tinos text-3xl sm:text-4xl md:text-5xl font-normal mb-3">
            {artist.name}
          </h1>
          {artist.genres.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3">
              {artist.genres.map((genre) => (
                <span
                  key={genre}
                  className="font-lexend font-light text-xs px-3 py-1 rounded-full border border-stroke text-muted"
                >
                  {genre}
                </span>
              ))}
            </div>
          )}
          {artist.popularity !== null && (
            <div className="font-lexend font-light text-sm text-muted mb-2">
              Popularity: {artist.popularity}/100
            </div>
          )}
          <div className="font-lexend font-light text-lg text-foreground">
            {artist.total_play_count.toLocaleString()} plays
          </div>
        </div>
      </div>

      <TimelineSelector
        onTimeframeChange={handleTimeframeChange}
        availableYears={initialPeriods.years}
        earliestDate={initialPeriods.earliest_date}
        latestDate={initialPeriods.latest_date}
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
          {/* Listening timeline */}
          <div>
            <h2 className="font-lexend text-xl sm:text-2xl font-normal mb-4">
              Listening Over Time
            </h2>
            <ListeningTimeline data={timeline} granularity={granularity} />
          </div>

          <MediaSection title="Top Songs" items={topSongs} type="songs" timeframe={currentTimeframe} date={currentDate} />
          <MediaSection title="Top Albums" items={topAlbums} type="albums" />
        </div>
      )}
    </>
  );
}
