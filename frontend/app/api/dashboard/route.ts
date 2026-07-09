import { NextResponse, type NextRequest } from 'next/server';
import type {
  Album,
  Artist,
  AvailablePeriods,
  DashboardResponse,
  ListeningStats,
  Song,
  TimelineResponse,
} from '@/app/types';
import { DASHBOARD_CACHE_CONTROL, DASHBOARD_NO_STORE_CACHE_CONTROL } from './cache';

const API_URL =
  process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

/**
 * Sections combined by this endpoint, in the order used to build the
 * response body. Kept as a const tuple so success/failure bookkeeping and
 * the response shape can't drift apart.
 */
const SECTION_KEYS = ['songs', 'artists', 'albums', 'stats', 'timeline', 'periods'] as const;
const TIMEFRAMES = new Set(['all', 'year', 'month']);

// This route always makes fresh upstream requests and applies its own
// Cache-Control header (see below), independent of Next.js's data cache and
// of any caching the backend does internally.
export const dynamic = 'force-dynamic';

interface SectionResult<T> {
  ok: boolean;
  data: T | null;
}

function canonicalDashboardQuery(searchParams: URLSearchParams): string | null {
  if ([...searchParams.keys()].some((key) => key !== 'timeframe' && key !== 'date')) {
    return null;
  }

  const timeframes = searchParams.getAll('timeframe');
  const dates = searchParams.getAll('date');
  if (timeframes.length > 1 || dates.length > 1 || (dates.length === 1 && timeframes.length === 0)) {
    return null;
  }

  const canonical = new URLSearchParams();
  let timeframe: string | null = null;
  if (timeframes.length === 1) {
    timeframe = timeframes[0];
    if (!TIMEFRAMES.has(timeframe)) return null;
    canonical.set('timeframe', timeframe);
  }

  if (
    (timeframe === 'all' && dates.length !== 0) ||
    ((timeframe === 'year' || timeframe === 'month') && dates.length !== 1)
  ) {
    return null;
  }

  if (dates.length === 1) {
    const date = dates[0];
    const parsed = new Date(`${date}T00:00:00Z`);
    const tomorrow = new Date();
    tomorrow.setUTCDate(tomorrow.getUTCDate() + 1);
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(date) ||
      Number.isNaN(parsed.valueOf()) ||
      parsed.toISOString().slice(0, 10) !== date ||
      date < '2000-01-01' ||
      parsed > tomorrow
    ) {
      return null;
    }
    if ((timeframe === 'year' && !date.endsWith('-01-01')) || date.slice(8) !== '01') {
      return null;
    }
    canonical.set('date', date);
  }

  const query = canonical.toString();
  return query === searchParams.toString() ? query : null;
}

async function fetchSection<T>(path: string): Promise<SectionResult<T>> {
  try {
    const res = await fetch(`${API_URL}${path}`, { cache: 'no-store' });
    if (!res.ok) {
      return { ok: false, data: null };
    }
    return { ok: true, data: (await res.json()) as T };
  } catch {
    return { ok: false, data: null };
  }
}

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const query = canonicalDashboardQuery(searchParams);
  if (query === null) {
    return NextResponse.json(
      {
        error: {
          code: 'INVALID_QUERY',
          message: 'Only canonical timeframe and date parameters are accepted.',
        },
      },
      { status: 400, headers: { 'Cache-Control': DASHBOARD_NO_STORE_CACHE_CONTROL } },
    );
  }
  const suffix = query ? `?${query}` : '';

  const [songs, artists, albums, stats, timeline, periods] = await Promise.all([
    fetchSection<Song[]>(`/api/top/songs${suffix}`),
    fetchSection<Artist[]>(`/api/top/artists${suffix}`),
    fetchSection<Album[]>(`/api/top/albums${suffix}`),
    fetchSection<ListeningStats>(`/api/top/stats${suffix}`),
    fetchSection<TimelineResponse>(`/api/top/timeline${suffix}`),
    fetchSection<AvailablePeriods>('/api/top/available-periods'),
  ]);

  const results = { songs, artists, albums, stats, timeline, periods };
  const errors = SECTION_KEYS.filter((key) => !results[key].ok);

  if (errors.length === SECTION_KEYS.length) {
    return NextResponse.json(
      {
        error: {
          code: 'UPSTREAM_UNAVAILABLE',
          message: 'Failed to load dashboard data.',
        },
      },
      { status: 502, headers: { 'Cache-Control': DASHBOARD_NO_STORE_CACHE_CONTROL } },
    );
  }

  const body: DashboardResponse = {
    songs: songs.data ?? [],
    artists: artists.data ?? [],
    albums: albums.data ?? [],
    stats: stats.data,
    timeline: timeline.data,
    periods: periods.data,
    errors,
  };

  // Only cache fully-successful responses for five minutes. A response with
  // any partial upstream failure is served but not cached, so a transient
  // backend hiccup doesn't get stuck on the edge.
  const cacheControl =
    errors.length > 0 ? DASHBOARD_NO_STORE_CACHE_CONTROL : DASHBOARD_CACHE_CONTROL;

  return NextResponse.json(body, {
    headers: { 'Cache-Control': cacheControl },
  });
}
