'use client';

import { useMemo } from 'react';
import Dropdown from '@/app/components/Dropdown';

interface TimelineSelectorProps {
  onTimeframeChange: (timeframe: string, date: string | null) => void;
  availableYears: number[];
  earliestDate: string | null;
  latestDate: string | null;
  selectedTimeframe: string;
  selectedDate: string | null;
  className?: string;
}

export default function TimelineSelector({
  onTimeframeChange,
  availableYears,
  earliestDate,
  latestDate,
  selectedTimeframe,
  selectedDate,
  className,
}: TimelineSelectorProps) {
  const timeframe = selectedTimeframe as 'all' | 'year' | 'month';
  const months = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const timeframeOptions: Array<'all' | 'year' | 'month'> = ['all', 'year', 'month'];

  const parsedDate = useMemo(() => {
    if (!selectedDate) return { year: undefined, month: null };
    const parts = selectedDate.split('-');
    if (parts.length < 2) return { year: undefined, month: null };
    const y = parseInt(parts[0], 10);
    const m = parseInt(parts[1], 10);
    return {
      year: isNaN(y) ? undefined : y,
      month: isNaN(m) ? null : m - 1
    };
  }, [selectedDate]);

  const displayYear = parsedDate.year;
  const displayMonth = parsedDate.month;

  const filteredMonths = useMemo(() => {
    if (!displayYear) return months;

    let startIndex = 0;
    let endIndex = 11;

    if (earliestDate) {
      const earliestYear = parseInt(earliestDate.split('-')[0], 10);
      const earliestMonth = parseInt(earliestDate.split('-')[1], 10) - 1;
      if (displayYear === earliestYear) startIndex = earliestMonth;
    }

    if (latestDate) {
      const latestYear = parseInt(latestDate.split('-')[0], 10);
      const latestMonth = parseInt(latestDate.split('-')[1], 10) - 1;
      if (displayYear === latestYear) endIndex = latestMonth;
    }

    return months.slice(startIndex, endIndex + 1);
  }, [displayYear, earliestDate, latestDate, months]);

  const formatDate = (year: number, month: number) => `${year}-${String(month + 1).padStart(2, '0')}-01`;

  const handleTypeChange = (newTimeframe: 'all' | 'year' | 'month') => {
    if (newTimeframe === 'all') {
      onTimeframeChange('all', null);
      return;
    }

    const year = displayYear ?? (availableYears.length > 0 ? availableYears[availableYears.length - 1] : new Date().getFullYear());
    const month = newTimeframe === 'month' ? (displayMonth ?? 0) : 0;
    onTimeframeChange(newTimeframe, formatDate(year, month));
  };

  const handleYearChange = (year: number) => {
    let month = timeframe === 'month' ? (displayMonth ?? 0) : 0;

    // Clamp month to the available range for the new year
    if (timeframe === 'month') {
      let minMonth = 0;
      let maxMonth = 11;

      if (earliestDate) {
        const earliestYear = parseInt(earliestDate.split('-')[0], 10);
        if (year === earliestYear) minMonth = parseInt(earliestDate.split('-')[1], 10) - 1;
      }
      if (latestDate) {
        const latestYear = parseInt(latestDate.split('-')[0], 10);
        if (year === latestYear) maxMonth = parseInt(latestDate.split('-')[1], 10) - 1;
      }

      month = Math.max(minMonth, Math.min(maxMonth, month));
    }

    onTimeframeChange(timeframe, formatDate(year, month));
  };

  const handleMonthChange = (monthName: string) => {
    const monthIndex = months.indexOf(monthName);
    if (monthIndex === -1) return;
    const year = displayYear ?? (availableYears.length > 0 ? availableYears[availableYears.length - 1] : new Date().getFullYear());
    onTimeframeChange('month', formatDate(year, monthIndex));
  };

  const formatTimeframe = (tf: 'all' | 'year' | 'month') => tf === 'all' ? 'All Time' : tf.charAt(0).toUpperCase() + tf.slice(1);

  return (
    <div className={className ?? "mb-6 sm:mb-8"}>
      <div className="flex items-start sm:items-center gap-4 sm:gap-6 flex-wrap font-['lexend']">
        <div>
          <label className="block text-sm font-light text-[#F4F4F9] mb-2">View By</label>
          <Dropdown
            options={timeframeOptions}
            selected={timeframe}
            onSelect={handleTypeChange}
            formatLabel={formatTimeframe}
            label="View by timeframe"
          />
        </div>

        {timeframe !== 'all' && availableYears.length > 0 && (
          <div>
            <label className="block text-sm font-light text-[#F4F4F9] mb-2">Year</label>
            <Dropdown
              options={availableYears}
              selected={displayYear ?? availableYears[availableYears.length - 1]}
              onSelect={handleYearChange}
              label="Select year"
            />
          </div>
        )}

        {timeframe === 'month' && (
          <div>
            <label className="block text-sm font-light text-[#F4F4F9] mb-2">Month</label>
            <Dropdown
              options={filteredMonths}
              selected={displayMonth !== null ? months[displayMonth] : filteredMonths[0]}
              onSelect={handleMonthChange}
              label="Select month"
            />
          </div>
        )}
      </div>
    </div>
  );
}
