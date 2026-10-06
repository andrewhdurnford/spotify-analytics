import type { TrackDetailResponse } from '@/app/types';
import { snapToAvailablePeriod } from '@/app/lib/timeframe';
import SongPage from './SongPage';
import { apiFetch } from '@/app/lib/api';

export const dynamic = 'force-dynamic';

function getCurrentMonthStart() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
}

async function fetchTrack(
  id: string,
  isNameFallback: boolean,
  timeframe: string,
  date: string | null,
): Promise<TrackDetailResponse | null> {
  let path: string;
  if (isNameFallback) {
    const [trackName, artistName] = decodeURIComponent(id).split('--');
    const params = new URLSearchParams({ track_name: trackName, artist_name: artistName, timeframe });
    if (date) params.set('date', date);
    path = `/api/tracks/by-name?${params}`;
  } else {
    const params = new URLSearchParams({ timeframe });
    if (date) params.set('date', date);
    path = `/api/tracks/${id}?${params}`;
  }
  const res = await apiFetch(path);
  if (!res.ok) return null;
  return res.json();
}

interface PageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ timeframe?: string; date?: string }>;
}

export default async function SongDetailPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const query = await searchParams;

  const requestedTimeframe = query.timeframe || 'month';
  const requestedDate = requestedTimeframe === 'all' ? null : (query.date || getCurrentMonthStart());

  // Encoded name format: trackName--artistName (URL encoded)
  const isNameFallback = id.includes('--');

  let initialData: TrackDetailResponse | null = null;
  let initialTimeframe = requestedTimeframe;
  let initialDate = requestedDate;

  try {
    initialData = await fetchTrack(id, isNameFallback, requestedTimeframe, requestedDate);
    if (initialData) {
      const snapped = snapToAvailablePeriod(
        { timeframe: requestedTimeframe, date: requestedDate },
        initialData.available_periods,
      );
      if (snapped.timeframe !== requestedTimeframe || snapped.date !== requestedDate) {
        const refetched = await fetchTrack(id, isNameFallback, snapped.timeframe, snapped.date);
        if (refetched) {
          initialData = refetched;
          initialTimeframe = snapped.timeframe;
          initialDate = snapped.date;
        }
      }
    }
  } catch (err) {
    console.error('Failed to fetch track detail:', err);
  }

  if (!initialData) {
    return (
      <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
        <div className="text-center py-12">
          <div className="font-lexend font-light text-lg">Song not found.</div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
      <SongPage
        songId={id}
        isNameFallback={isNameFallback}
        initialData={initialData}
        initialTimeframe={initialTimeframe}
        initialDate={initialDate}
      />
    </main>
  );
}
