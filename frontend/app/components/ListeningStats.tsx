'use client';

import type { ListeningStats as ListeningStatsData } from '@/app/types';

interface ListeningStatsProps {
  stats: ListeningStatsData | undefined;
}

interface StatItem {
  label: string;
  value: string;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-US').format(value);
}

function formatMinutes(ms: number): string {
  const minutes = Math.round(ms / 60000);
  return formatNumber(minutes);
}

export default function ListeningStats({ stats }: ListeningStatsProps) {
  if (!stats || stats.total_play_count === 0) {
    return (
      <section>
        <h2 className="font-lexend text-xl sm:text-2xl font-normal mb-4">Period Stats</h2>
        <p className="font-lexend font-light text-soft">
          No listening stats available for this period
        </p>
      </section>
    );
  }

  const statItems: StatItem[] = [
    { label: 'Minutes listened', value: formatMinutes(stats.total_listening_ms) },
    { label: 'Plays', value: formatNumber(stats.total_play_count) },
    { label: 'Songs', value: formatNumber(stats.unique_songs) },
    { label: 'Artists', value: formatNumber(stats.unique_artists) },
    { label: 'Albums', value: formatNumber(stats.unique_albums) },
    { label: 'Listening days', value: formatNumber(stats.active_days) },
  ];

  return (
    <section>
      <h2 className="font-lexend text-xl sm:text-2xl font-normal mb-4">Period Stats</h2>
      <div className="grid grid-cols-stat-summary gap-x-7 gap-y-3 justify-start font-lexend">
        {statItems.map((item) => (
          <div
            key={item.label}
            className="flex items-baseline gap-2"
          >
            <span className="text-xl sm:text-2xl font-normal text-foreground">
              {item.value}
            </span>
            <span className="font-light text-xs sm:text-sm text-muted whitespace-nowrap">
              {item.label}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
