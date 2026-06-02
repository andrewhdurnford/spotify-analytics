import type { AlbumDetailResponse } from '@/app/types';
import { snapToAvailablePeriod } from '@/app/lib/timeframe';
import AlbumPage from './AlbumPage';

export const dynamic = 'force-dynamic';

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function getCurrentMonthStart() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
}

function parseSlug(id: string): { artistId: string; albumName: string } | null {
  const decoded = decodeURIComponent(id);
  const sep = decoded.indexOf('--');
  if (sep === -1) return null;
  const artistId = decoded.slice(0, sep);
  const albumName = decoded.slice(sep + 2);
  if (!artistId || !albumName) return null;
  return { artistId, albumName };
}

async function fetchAlbum(
  artistId: string,
  albumName: string,
  timeframe: string,
  date: string | null,
): Promise<AlbumDetailResponse | null> {
  const params = new URLSearchParams({ artist_id: artistId, album_name: albumName, timeframe });
  if (date) params.set('date', date);
  const res = await fetch(`${API_URL}/api/albums/by-name?${params}`);
  if (!res.ok) return null;
  return res.json();
}

interface PageProps {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ timeframe?: string; date?: string }>;
}

export default async function AlbumDetailPage({ params, searchParams }: PageProps) {
  const { id } = await params;
  const query = await searchParams;

  const requestedTimeframe = query.timeframe || 'month';
  const requestedDate = requestedTimeframe === 'all' ? null : (query.date || getCurrentMonthStart());

  const parsed = parseSlug(id);

  let initialData: AlbumDetailResponse | null = null;
  let initialTimeframe = requestedTimeframe;
  let initialDate = requestedDate;

  if (parsed) {
    try {
      initialData = await fetchAlbum(parsed.artistId, parsed.albumName, requestedTimeframe, requestedDate);
      if (initialData) {
        const snapped = snapToAvailablePeriod(
          { timeframe: requestedTimeframe, date: requestedDate },
          initialData.available_periods,
        );
        if (snapped.timeframe !== requestedTimeframe || snapped.date !== requestedDate) {
          const refetched = await fetchAlbum(parsed.artistId, parsed.albumName, snapped.timeframe, snapped.date);
          if (refetched) {
            initialData = refetched;
            initialTimeframe = snapped.timeframe;
            initialDate = snapped.date;
          }
        }
      }
    } catch (err) {
      console.error('Failed to fetch album detail:', err);
    }
  }

  if (!initialData || !parsed) {
    return (
      <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
        <div className="text-center py-12">
          <div className="font-lexend font-light text-lg">Album not found.</div>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
      <AlbumPage
        artistId={parsed.artistId}
        albumName={parsed.albumName}
        initialData={initialData}
        initialTimeframe={initialTimeframe}
        initialDate={initialDate}
      />
    </main>
  );
}
