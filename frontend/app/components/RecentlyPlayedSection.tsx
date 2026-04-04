'use client';

import Image from 'next/image';
import Link from 'next/link';
import type { RecentlyPlayedItem } from '@/app/types';

interface RecentlyPlayedSectionProps {
  title: string;
  items: RecentlyPlayedItem[];
  showFullHistoryLink?: boolean;
}

function formatPlayedAt(playedAt: string): string {
  const hasTimezone = /(?:Z|[+-]\d{2}:\d{2})$/.test(playedAt);
  const date = new Date(hasTimezone ? playedAt : `${playedAt}Z`);
  if (Number.isNaN(date.getTime())) {
    return playedAt;
  }

  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  }).format(date);
}

function buildSongHref(item: RecentlyPlayedItem): string {
  if (item.track_id) {
    return `/songs/${item.track_id}?timeframe=all`;
  }

  const encoded = encodeURIComponent(`${item.track_name}--${item.artist_name}`);
  return `/songs/${encoded}?timeframe=all`;
}

function getRowKey(item: RecentlyPlayedItem, idx: number): string {
  const trackKey = item.track_id ?? `${item.track_name}-${item.artist_name}`;
  return `${trackKey}-${item.played_at}-${idx}`;
}

export default function RecentlyPlayedSection({
  title,
  items,
  showFullHistoryLink = false,
}: RecentlyPlayedSectionProps) {
  return (
    <div>
      <h2 className="font-['lexend'] text-xl sm:text-2xl font-normal mb-4">{title}</h2>

      {items.length === 0 ? (
        <p className="font-['lexend'] font-light text-[#D1D1D7]">
          No recent listening history available.
        </p>
      ) : (
        <div className="rounded-lg border border-white/10 bg-white/[0.02] divide-y divide-white/10 overflow-hidden">
          {items.map((item, idx) => (
            <Link
              key={getRowKey(item, idx)}
              href={buildSongHref(item)}
              className="block hover:bg-white/[0.04] transition-colors"
            >
              <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 sm:gap-6 px-3 sm:px-4 py-3">
                <div className="flex items-center gap-3 min-w-0">
                  {item.album_image ? (
                    <Image
                      src={item.album_image}
                      alt={`${item.track_name} cover`}
                      width={48}
                      height={48}
                      className="w-10 h-10 sm:w-12 sm:h-12 rounded-md object-cover shrink-0"
                    />
                  ) : (
                    <div className="w-10 h-10 sm:w-12 sm:h-12 rounded-md bg-white/10 shrink-0" />
                  )}

                  <div className="min-w-0">
                    <div className="font-['lexend'] font-normal text-sm sm:text-base truncate">
                      {item.track_name}
                    </div>
                    <div className="font-['lexend'] font-light text-xs sm:text-sm text-[#A1A1A7] truncate">
                      {item.artist_name}
                    </div>
                  </div>
                </div>

                <div className="font-['lexend'] font-light text-xs sm:text-sm text-[#A1A1A7] tabular-nums whitespace-nowrap text-right">
                  {formatPlayedAt(item.played_at)}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      {showFullHistoryLink && (
        <div className="mt-4 flex justify-center">
          <Link
            href="/history"
            className="font-['lexend'] font-light text-sm border border-white/20 rounded-full px-4 py-2 hover:border-white/40 hover:bg-white/[0.04] transition-colors"
          >
            See Full History
          </Link>
        </div>
      )}
    </div>
  );
}
