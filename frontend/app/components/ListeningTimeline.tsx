'use client';

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts';
import type { PlayCountOverTime } from '@/app/types';

interface ListeningTimelineProps {
  data: PlayCountOverTime[];
  granularity: string;
}

const chartTheme = {
  axis: 'var(--color-chart-axis)',
  background: 'var(--color-deep-blue)',
  foreground: 'var(--color-foreground)',
  grid: 'var(--color-chart-grid)',
  muted: 'var(--color-muted)',
  spotify: 'var(--color-spotify)',
  fontFamily: 'var(--font-lexend)',
  tickFontSize: 'var(--text-chart-tick)',
  tooltipFontSize: 'var(--text-chart-tooltip)',
};

function formatMonth(period: string): string {
  const [year, mo] = period.split('-');
  const date = new Date(Number(year), Number(mo) - 1);
  return date.toLocaleDateString('en-US', { month: 'short' }) + " '" + date.toLocaleDateString('en-US', { year: '2-digit' });
}

function formatDay(period: string): string {
  const date = new Date(period + 'T00:00:00');
  return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function formatPeriod(period: string, granularity: string): string {
  return granularity === 'daily' ? formatDay(period) : formatMonth(period);
}

export default function ListeningTimeline({ data, granularity }: ListeningTimelineProps) {
  if (data.length === 0) {
    return (
      <div className="font-lexend font-light text-muted text-sm">
        No listening history available.
      </div>
    );
  }

  return (
    <div className="w-full h-64 sm:h-80">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
          <XAxis
            dataKey="period"
            tickFormatter={(val) => formatPeriod(String(val), granularity)}
            tick={{
              fill: chartTheme.muted,
              fontSize: chartTheme.tickFontSize,
              fontFamily: chartTheme.fontFamily,
            }}
            axisLine={{ stroke: chartTheme.axis }}
            tickLine={false}
            interval="preserveStartEnd"
            minTickGap={40}
          />
          <YAxis
            tick={{
              fill: chartTheme.muted,
              fontSize: chartTheme.tickFontSize,
              fontFamily: chartTheme.fontFamily,
            }}
            axisLine={{ stroke: chartTheme.axis }}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: chartTheme.background,
              border: `1px solid ${chartTheme.axis}`,
              borderRadius: '8px',
              fontFamily: chartTheme.fontFamily,
              fontSize: chartTheme.tooltipFontSize,
              color: chartTheme.foreground,
            }}
            labelFormatter={(label) => formatPeriod(String(label), granularity)}
            formatter={(value) => [String(value), 'Plays']}
          />
          <Line
            type="monotone"
            dataKey="play_count"
            stroke={chartTheme.spotify}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: chartTheme.spotify }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
