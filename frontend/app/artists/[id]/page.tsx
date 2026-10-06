import type { ArtistDetailResponse } from '@/app/types';
import { snapToAvailablePeriod } from '@/app/lib/timeframe';
import ArtistPage from './ArtistPage';
import { apiFetch } from '@/app/lib/api';

export const dynamic = 'force-dynamic';

function getCurrentMonthStart() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
}

async function fetchArtist(
  id: string,
  timeframe: string,
  date: string | null,
): Promise<ArtistDetailResponse | null> {
  const params = new URLSearchParams({ timeframe });
  if (date) params.set('date', date);
  const res = await apiFetch(`/api/artists/${id}?${params}`);
  if (!res.ok) return null;
  return res.json();
}

interface PageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ timeframe?: string; date?: string }>;
}

export default async function ArtistDetailPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const query = await searchParams;

  const requestedTimeframe = query.timeframe || 'month';
  const requestedDate = requestedTimeframe === 'all' ? null : (query.date || getCurrentMonthStart());

  let initialData: ArtistDetailResponse | null = null;
  let initialTimeframe = requestedTimeframe;
  let initialDate = requestedDate;

  try {
    initialData = await fetchArtist(id, requestedTimeframe, requestedDate);
    if (initialData) {
      const snapped = snapToAvailablePeriod(
        { timeframe: requestedTimeframe, date: requestedDate },
        initialData.available_periods,
      );
      if (snapped.timeframe !== requestedTimeframe || snapped.date !== requestedDate) {
        const refetched = await fetchArtist(id, snapped.timeframe, snapped.date);
        if (refetched) {
          initialData = refetched;
          initialTimeframe = snapped.timeframe;
          initialDate = snapped.date;
        }
      }
    }
  } catch (err) {
    console.error('Failed to fetch artist detail:', err);
  }

  if (!initialData) {
    return (
      <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
        <div className="text-center py-12">
          <div className="font-lexend font-light text-lg">Artist not found.</div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
      <ArtistPage
        artistId={id}
        initialData={initialData}
        initialTimeframe={initialTimeframe}
        initialDate={initialDate}
      />
    </main>
  );
}
