'use client';

import { useState, useMemo } from 'react';
import useSWR from 'swr';
import TimelineSelector from '@/app/components/TimelineSelector';
import CurrentlyPlaying from '@/app/components/CurrentlyPlaying';
import ListeningStats from '@/app/components/ListeningStats';
import MediaSection from '@/app/components/MediaSection';
import RecentlyPlayedSection from '@/app/components/RecentlyPlayedSection';
import { fetcher } from '@/app/lib/fetcher';
import type {
  Song,
  Artist,
  Album,
  AvailablePeriods,
  ListeningStats as ListeningStatsData,
  RecentlyPlayedItem,
  RecentlyPlayedResponse,
} from '@/app/types';

interface DashboardProps {
  initialSongs: Song[];
  initialArtists: Artist[];
  initialAlbums: Album[];
  initialStats: ListeningStatsData;
  initialRecentlyPlayed: RecentlyPlayedItem[];
  initialPeriods: AvailablePeriods;
  initialTimeframe: string;
  initialDate: string | null;
}

export default function Dashboard({
  initialSongs,
  initialArtists,
  initialAlbums,
  initialStats,
  initialRecentlyPlayed,
  initialPeriods,
  initialTimeframe,
  initialDate,
}: DashboardProps) {
  const [currentTimeframe, setCurrentTimeframe] = useState(initialTimeframe);
  const [currentDate, setCurrentDate] = useState<string | null>(initialDate);

  const params = useMemo(() => {
    const p = new URLSearchParams({ timeframe: currentTimeframe });
    if (currentDate) p.set('date', currentDate);
    return p.toString();
  }, [currentTimeframe, currentDate]);

  const {
    data: songs,
    error: songsError,
    isLoading: songsLoading,
  } = useSWR<Song[]>(`/api/top/songs?${params}`, fetcher, {
    fallbackData: initialSongs,
  });

  const {
    data: artists,
    error: artistsError,
    isLoading: artistsLoading,
  } = useSWR<Artist[]>(`/api/top/artists?${params}`, fetcher, {
    fallbackData: initialArtists,
  });

  const {
    data: albums,
    error: albumsError,
    isLoading: albumsLoading,
  } = useSWR<Album[]>(`/api/top/albums?${params}`, fetcher, {
    fallbackData: initialAlbums,
  });

  const {
    data: stats,
    error: statsError,
    isLoading: statsLoading,
  } = useSWR<ListeningStatsData>(`/api/top/stats?${params}`, fetcher, {
    fallbackData: initialStats,
  });

  const {
    data: recentlyPlayed,
    error: recentlyPlayedError,
    isLoading: recentlyPlayedLoading,
  } = useSWR<RecentlyPlayedResponse>('/api/top/recently-played?page=1&page_size=10', fetcher, {
    fallbackData: {
      items: initialRecentlyPlayed,
      page: 1,
      page_size: 10,
      has_next: false,
    },
  });

  const loading = songsLoading || artistsLoading || albumsLoading || statsLoading || recentlyPlayedLoading;
  const error = songsError || artistsError || albumsError || statsError || recentlyPlayedError;

  const handleTimeframeChange = (timeframe: string, date: string | null) => {
    setCurrentTimeframe(timeframe);
    setCurrentDate(date);
  };

  return (
    <>
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-6 mt-6 sm:mt-12 gap-4">
        <div className="flex-1 min-w-0">
          <h1 className="font-tinos text-3xl sm:text-4xl md:text-5xl font-normal mb-6 sm:mb-8">Spotify Stats</h1>
          <TimelineSelector
            onTimeframeChange={handleTimeframeChange}
            availableYears={initialPeriods.years}
            earliestDate={initialPeriods.earliest_date}
            latestDate={initialPeriods.latest_date}
            selectedTimeframe={currentTimeframe}
            selectedDate={currentDate}
            className=""
          />
        </div>
        <CurrentlyPlaying timeframe={currentTimeframe} date={currentDate} />
      </div>

      {error ? (
        <div className="text-center py-12">
          <div className="font-lexend font-light text-danger text-lg mb-4">
            Failed to load data. Please try again later.
          </div>
        </div>
      ) : loading ? (
        <div className="text-center py-12">
          <div className="text-lg sm:text-xl font-lexend font-light">Loading...</div>
        </div>
      ) : (
        <div className="space-y-6 sm:space-y-8">
          <ListeningStats stats={stats} />
          <MediaSection title="Top Songs" items={songs ?? []} type="songs" timeframe={currentTimeframe} date={currentDate} />
          <MediaSection title="Top Albums" items={albums ?? []} type="albums" />
          <MediaSection title="Top Artists" items={artists ?? []} type="artists" timeframe={currentTimeframe} date={currentDate} />
          <RecentlyPlayedSection
            title="Recently Played"
            items={recentlyPlayed?.items ?? []}
            showFullHistoryLink
          />
        </div>
      )}
    </>
  );
}
