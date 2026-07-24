'use client';

import { useState, useMemo } from 'react';
import { useRouter } from 'next/navigation';
import useSWR from 'swr';
import TimelineSelector from '@/app/components/TimelineSelector';
import CurrentlyPlaying from '@/app/components/CurrentlyPlaying';
import ListeningStats from '@/app/components/ListeningStats';
import ListeningTimeline from '@/app/components/ListeningTimeline';
import MediaSection from '@/app/components/MediaSection';
import RecentlyPlayedSection from '@/app/components/RecentlyPlayedSection';
import { fetcher } from '@/app/lib/fetcher';
import { timeframeQuery } from '@/app/lib/timeframe';
import type {
  Song,
  Artist,
  Album,
  AvailablePeriods,
  ListeningStats as ListeningStatsData,
  RecentlyPlayedResponse,
  TimelineResponse,
} from '@/app/types';

interface DashboardProps {
  initialTimeframe: string;
  initialDate: string | null;
}

function LoadingSection({ title, cards = false }: { title: string; cards?: boolean }) {
  return (
    <section aria-label={`Loading ${title}`}>
      <h2 className="font-lexend text-xl sm:text-2xl font-normal mb-4">{title}</h2>
      <div className={`flex ${cards ? 'gap-3' : 'gap-7'} animate-pulse`}>
        {Array.from({ length: cards ? 5 : 3 }, (_, index) => (
          <div
            key={index}
            className={
              cards
                ? 'w-32 h-40 sm:w-40 sm:h-48 rounded-md bg-subtle shrink-0'
                : 'w-32 h-8 rounded bg-subtle'
            }
          />
        ))}
      </div>
    </section>
  );
}

export default function Dashboard({
  initialTimeframe,
  initialDate,
}: DashboardProps) {
  const router = useRouter();
  const [currentTimeframe, setCurrentTimeframe] = useState(initialTimeframe);
  const [currentDate, setCurrentDate] = useState<string | null>(initialDate);

  const params = useMemo(
    () => timeframeQuery(currentTimeframe, currentDate),
    [currentTimeframe, currentDate],
  );

  const {
    data: songs,
    error: songsError,
    isLoading: songsLoading,
  } = useSWR<Song[]>(`/api/top/songs?${params}`, fetcher, { revalidateOnFocus: false });

  const {
    data: artists,
    error: artistsError,
    isLoading: artistsLoading,
  } = useSWR<Artist[]>(`/api/top/artists?${params}`, fetcher, { revalidateOnFocus: false });

  const {
    data: albums,
    error: albumsError,
    isLoading: albumsLoading,
  } = useSWR<Album[]>(`/api/top/albums?${params}`, fetcher, { revalidateOnFocus: false });

  const {
    data: stats,
    error: statsError,
    isLoading: statsLoading,
  } = useSWR<ListeningStatsData>(`/api/top/stats?${params}`, fetcher, { revalidateOnFocus: false });

  const {
    data: recentlyPlayed,
    error: recentlyPlayedError,
    isLoading: recentlyPlayedLoading,
  } = useSWR<RecentlyPlayedResponse>('/api/top/recently-played?page=1&page_size=10', fetcher, {
    refreshInterval: 5 * 60_000,
    revalidateOnFocus: false,
  });

  const { data: periods } = useSWR<AvailablePeriods>('/api/top/available-periods', fetcher, {
    revalidateOnFocus: false,
  });

  const { data: timelineData } = useSWR<TimelineResponse>(
    `/api/top/timeline?${params}`,
    fetcher,
    { revalidateOnFocus: false },
  );

  const timeline = timelineData?.timeline ?? [];
  const timelineGranularity = timelineData?.granularity ?? 'monthly';

  const error = songsError || artistsError || albumsError || statsError || recentlyPlayedError;

  const handleTimeframeChange = (timeframe: string, date: string | null) => {
    setCurrentTimeframe(timeframe);
    setCurrentDate(date);
    router.replace(`/?${timeframeQuery(timeframe, date)}`, { scroll: false });
  };

  return (
    <>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6 mt-6 sm:mt-12 gap-4">
        <div className="flex-1 min-w-0">
          <h1 className="font-tinos text-3xl sm:text-4xl md:text-5xl font-normal mb-6 sm:mb-8">Spotify Stats</h1>
          <TimelineSelector
            onTimeframeChange={handleTimeframeChange}
            availableYears={periods?.years ?? []}
            earliestDate={periods?.earliest_date ?? null}
            latestDate={periods?.latest_date ?? null}
            selectedTimeframe={currentTimeframe}
            selectedDate={currentDate}
            className=""
          />
        </div>
        <CurrentlyPlaying timeframe={currentTimeframe} date={currentDate} />
      </div>

      {error && (
        <div className="py-4">
          <div className="font-lexend font-light text-danger text-sm">
            Failed to load data. Please try again later.
          </div>
        </div>
      )}

      <div className="space-y-6 sm:space-y-8">
        {statsLoading ? <LoadingSection title="Period Stats" /> : <ListeningStats stats={stats} />}

        {timelineData ? (
          timeline.length > 0 && (
            <div>
              <h2 className="font-tinos text-xl sm:text-2xl font-normal mb-4">Listening Over Time</h2>
              <ListeningTimeline data={timeline} granularity={timelineGranularity} />
            </div>
          )
        ) : (
          <LoadingSection title="Listening Over Time" />
        )}

        {songsLoading ? (
          <LoadingSection title="Top Songs" cards />
        ) : (
          <MediaSection title="Top Songs" items={songs ?? []} type="songs" timeframe={currentTimeframe} date={currentDate} />
        )}
        {albumsLoading ? (
          <LoadingSection title="Top Albums" cards />
        ) : (
          <MediaSection title="Top Albums" items={albums ?? []} type="albums" timeframe={currentTimeframe} date={currentDate} />
        )}
        {artistsLoading ? (
          <LoadingSection title="Top Artists" cards />
        ) : (
          <MediaSection title="Top Artists" items={artists ?? []} type="artists" timeframe={currentTimeframe} date={currentDate} />
        )}
        {recentlyPlayedLoading ? (
          <LoadingSection title="Recently Played" cards />
        ) : (
          <RecentlyPlayedSection
            title="Recently Played"
            items={recentlyPlayed?.items ?? []}
            showFullHistoryLink
          />
        )}
      </div>
    </>
  );
}
