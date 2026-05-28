'use client';

import { useMemo, useState } from 'react';
import useSWR from 'swr';
import Breadcrumb from '@/app/components/Breadcrumb';
import RecentlyPlayedSection from '@/app/components/RecentlyPlayedSection';
import { fetcher } from '@/app/lib/fetcher';
import type { RecentlyPlayedResponse } from '@/app/types';

interface HistoryPageProps {
  initialData: RecentlyPlayedResponse;
}

export default function HistoryPage({ initialData }: HistoryPageProps) {
  const [startDate, setStartDate] = useState('');
  const [page, setPage] = useState(1);

  const swrKey = useMemo(() => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: '100',
    });
    if (startDate) {
      params.set('start_date', startDate);
    }
    return `/api/top/recently-played?${params.toString()}`;
  }, [page, startDate]);

  const { data, error, isLoading } = useSWR<RecentlyPlayedResponse>(swrKey, fetcher, {
    fallbackData: initialData,
    keepPreviousData: true,
  });

  const history = data ?? initialData;

  function handleStartDateChange(value: string) {
    setStartDate(value);
    setPage(1);
  }

  return (
    <>
      <Breadcrumb items={[{ label: 'Home', href: '/' }, { label: 'History' }]} />

      <div className="mb-6 sm:mb-8">
        <h1 className="font-tinos text-3xl sm:text-4xl md:text-5xl font-normal mb-4">Full Listening History</h1>
        <p className="font-lexend font-light text-sm sm:text-base text-muted mb-4">
          Showing 100 plays per page in reverse chronological order.
        </p>

        <div className="flex flex-col sm:flex-row sm:items-end gap-4 font-lexend">
          <div>
            <label htmlFor="start-date" className="block text-sm font-light text-foreground mb-2">
              Start from date
            </label>
            <input
              id="start-date"
              type="date"
              value={startDate}
              onChange={(e) => handleStartDateChange(e.target.value)}
              className="bg-transparent border border-stroke rounded-md px-3 py-2 text-sm text-foreground focus:outline-none focus:border-stroke-strong"
            />
          </div>
        </div>
      </div>

      {error ? (
        <div className="text-center py-12">
          <div className="font-lexend font-light text-danger text-lg">
            Failed to load history. Please try again.
          </div>
        </div>
      ) : (
        <>
          <RecentlyPlayedSection title="History" items={history.items} />

          <div className="mt-6 flex items-center justify-between gap-4 font-lexend">
            <button
              type="button"
              onClick={() => setPage((prev) => Math.max(prev - 1, 1))}
              disabled={page === 1 || isLoading}
              className="text-sm border border-stroke rounded-full px-4 py-2 disabled:opacity-40 disabled:cursor-not-allowed hover:border-stroke-strong hover:bg-surface-hover transition-colors"
            >
              Previous
            </button>

            <div className="font-light text-sm text-muted">Page {page}</div>

            <button
              type="button"
              onClick={() => setPage((prev) => prev + 1)}
              disabled={!history.has_next || isLoading}
              className="text-sm border border-stroke rounded-full px-4 py-2 disabled:opacity-40 disabled:cursor-not-allowed hover:border-stroke-strong hover:bg-surface-hover transition-colors"
            >
              Next
            </button>
          </div>
        </>
      )}
    </>
  );
}
