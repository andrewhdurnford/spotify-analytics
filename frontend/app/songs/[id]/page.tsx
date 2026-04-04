import type { TrackDetailResponse, AvailablePeriods } from '@/app/types';
import SongPage from './SongPage';

export const dynamic = 'force-dynamic';

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function getCurrentMonthStart() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
}

interface PageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ timeframe?: string; date?: string }>;
}

export default async function SongDetailPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const query = await searchParams;

  // Use timeframe from URL search params if provided, otherwise default to current month
  const initialTimeframe = query.timeframe || 'month';
  const initialDate = initialTimeframe === 'all' ? null : (query.date || getCurrentMonthStart());

  let initialData: TrackDetailResponse | null = null;
  let initialPeriods: AvailablePeriods = { years: [], earliest_date: null, latest_date: null };

  // Determine if this is a Spotify track ID or an encoded name fallback
  // Encoded name format: trackName--artistName (URL encoded)
  const isNameFallback = id.includes('--');

  try {
    let detailUrl: string;
    if (isNameFallback) {
      const [trackName, artistName] = decodeURIComponent(id).split('--');
      const nameParams = new URLSearchParams({
        track_name: trackName,
        artist_name: artistName,
        timeframe: initialTimeframe,
      });
      if (initialDate) nameParams.set('date', initialDate);
      detailUrl = `${API_URL}/api/tracks/by-name?${nameParams}`;
    } else {
      const detailParams = new URLSearchParams({ timeframe: initialTimeframe });
      if (initialDate) detailParams.set('date', initialDate);
      detailUrl = `${API_URL}/api/tracks/${id}?${detailParams}`;
    }

    const [detailRes, periodsRes] = await Promise.all([
      fetch(detailUrl),
      fetch(`${API_URL}/api/top/available-periods`),
    ]);

    if (detailRes.ok) initialData = await detailRes.json();
    if (periodsRes.ok) initialPeriods = await periodsRes.json();
  } catch (err) {
    console.error('Failed to fetch track detail:', err);
  }

  if (!initialData) {
    return (
      <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-[#F4F4F9]">
        <div className="text-center py-12">
          <div className="font-['lexend'] font-light text-lg">Song not found.</div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-[#F4F4F9]">
      <SongPage
        songId={id}
        isNameFallback={isNameFallback}
        initialData={initialData}
        initialPeriods={initialPeriods}
        initialTimeframe={initialTimeframe}
        initialDate={initialDate}
      />
    </main>
  );
}
