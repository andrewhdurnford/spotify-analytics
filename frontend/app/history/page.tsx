import HistoryPage from './HistoryPage';
import type { RecentlyPlayedResponse } from '@/app/types';
import { apiFetch } from '@/app/lib/api';

export const dynamic = 'force-dynamic';

const EMPTY_HISTORY: RecentlyPlayedResponse = {
  items: [],
  page: 1,
  page_size: 100,
  has_next: false,
};

export default async function HistoryRoutePage() {
  let initialData: RecentlyPlayedResponse = EMPTY_HISTORY;

  try {
    const historyRes = await apiFetch(`/api/top/recently-played?page=1&page_size=100`);
    if (historyRes.ok) {
      initialData = await historyRes.json();
    }
  } catch (err) {
    console.error('Failed to fetch history page data:', err);
  }

  return (
    <main className="min-h-screen p-4 sm:p-6 md:p-8 max-w-7xl mx-auto text-foreground">
      <HistoryPage initialData={initialData} />
    </main>
  );
}
