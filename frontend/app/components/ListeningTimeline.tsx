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
      <div className="font-['lexend'] font-light text-[#A1A1A7] text-sm">
        No listening history available.
      </div>
    );
  }

  return (
    <div className="w-full h-64 sm:h-80">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
          <XAxis
            dataKey="period"
            tickFormatter={(val) => formatPeriod(String(val), granularity)}
            tick={{ fill: '#A1A1A7', fontSize: 12, fontFamily: 'lexend' }}
            axisLine={{ stroke: 'rgba(255,255,255,0.12)' }}
            tickLine={false}
            interval="preserveStartEnd"
            minTickGap={40}
          />
          <YAxis
            tick={{ fill: '#A1A1A7', fontSize: 12, fontFamily: 'lexend' }}
            axisLine={{ stroke: 'rgba(255,255,255,0.12)' }}
            tickLine={false}
            allowDecimals={false}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: '#001A23',
              border: '1px solid rgba(255,255,255,0.12)',
              borderRadius: '8px',
              fontFamily: 'lexend',
              fontSize: '13px',
              color: '#F4F4F9',
            }}
            labelFormatter={(label) => formatPeriod(String(label), granularity)}
            formatter={(value) => [String(value), 'Plays']}
          />
          <Line
            type="monotone"
            dataKey="play_count"
            stroke="#1DB954"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: '#1DB954' }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
