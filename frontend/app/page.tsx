import Dashboard from '@/app/components/Dashboard';
import type {
  Song,
  Artist,
  Album,
  AvailablePeriods,
  ListeningStats,
  RecentlyPlayedItem,
  RecentlyPlayedResponse,
} from '@/app/types';

export const dynamic = 'force-dynamic';

const API_URL = process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

function getCurrentMonthStart() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-01`;
}

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ timeframe?: string; date?: string }>;
}) {
  const query = await searchParams;

  // Use timeframe from URL search params if provided, otherwise default to current month
  const initialTimeframe = query.timeframe || 'month';
  const initialDate = initialTimeframe === 'all' ? null : (query.date || getCurrentMonthStart());

  // Fetch initial data on the server
  let initialSongs: Song[] = [];
  let initialArtists: Artist[] = [];
  let initialAlbums: Album[] = [];
  let initialStats: ListeningStats = {
    total_play_count: 0,
    total_listening_ms: 0,
    unique_songs: 0,
    unique_artists: 0,
    unique_albums: 0,
    active_days: 0,
    average_play_ms: 0,
  };
  let initialRecentlyPlayed: RecentlyPlayedItem[] = [];
  let initialPeriods: AvailablePeriods = { years: [], earliest_date: null, latest_date: null };

  try {
    const params = new URLSearchParams({ timeframe: initialTimeframe });
    if (initialDate) params.set('date', initialDate);

    const [songsRes, artistsRes, albumsRes, statsRes, recentlyPlayedRes, periodsRes] = await Promise.all([
      fetch(`${API_URL}/api/top/songs?${params}`),
      fetch(`${API_URL}/api/top/artists?${params}`),
      fetch(`${API_URL}/api/top/albums?${params}`),
      fetch(`${API_URL}/api/top/stats?${params}`),
      fetch(`${API_URL}/api/top/recently-played?page=1&page_size=10`),
      fetch(`${API_URL}/api/top/available-periods`),
    ]);

    if (songsRes.ok) initialSongs = await songsRes.json();
    if (artistsRes.ok) initialArtists = await artistsRes.json();
    if (albumsRes.ok) initialAlbums = await albumsRes.json();
    if (statsRes.ok) initialStats = await statsRes.json();
    if (recentlyPlayedRes.ok) {
      const payload: RecentlyPlayedResponse = await recentlyPlayedRes.json();
      initialRecentlyPlayed = payload.items;
    }
    if (periodsRes.ok) initialPeriods = await periodsRes.json();
  } catch (err) {
    // Server-side fetch failed -- Dashboard will render with empty data
    // and the client can retry
    console.error('Failed to fetch initial data:', err);
  }

  return (
    <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-[#F4F4F9]">
      <Dashboard
        initialSongs={initialSongs}
        initialArtists={initialArtists}
        initialAlbums={initialAlbums}
        initialStats={initialStats}
        initialRecentlyPlayed={initialRecentlyPlayed}
        initialPeriods={initialPeriods}
        initialTimeframe={initialTimeframe}
        initialDate={initialDate}
      />
    </main>
  );
}
