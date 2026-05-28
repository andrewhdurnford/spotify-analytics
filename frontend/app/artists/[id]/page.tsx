import type { ArtistDetailResponse, AvailablePeriods } from '@/app/types';
import ArtistPage from './ArtistPage';

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

export default async function ArtistDetailPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const query = await searchParams;

  // Use timeframe from URL search params if provided, otherwise default to current month
  const initialTimeframe = query.timeframe || 'month';
  const initialDate = initialTimeframe === 'all' ? null : (query.date || getCurrentMonthStart());

  let initialData: ArtistDetailResponse | null = null;
  let initialPeriods: AvailablePeriods = { years: [], earliest_date: null, latest_date: null };

  try {
    const detailParams = new URLSearchParams({ timeframe: initialTimeframe });
    if (initialDate) detailParams.set('date', initialDate);

    const [detailRes, periodsRes] = await Promise.all([
      fetch(`${API_URL}/api/artists/${id}?${detailParams}`),
      fetch(`${API_URL}/api/top/available-periods`),
    ]);

    if (detailRes.ok) initialData = await detailRes.json();
    if (periodsRes.ok) initialPeriods = await periodsRes.json();
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
        initialPeriods={initialPeriods}
        initialTimeframe={initialTimeframe}
        initialDate={initialDate}
      />
    </main>
  );
}
