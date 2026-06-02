import type { AvailablePeriods } from '@/app/types';

export interface TimeframeSelection {
  timeframe: string;
  date: string | null;
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return count === 1 ? singular : (plural ?? `${singular}s`);
}

/**
 * Snap a (timeframe, date) selection to the nearest period that has data,
 * given the per-item available_periods. 'all' is always valid. For 'year' and
 * 'month' selections that fall outside the available range, clamp to the
 * closest end of the range.
 */
export function snapToAvailablePeriod(
  selection: TimeframeSelection,
  periods: AvailablePeriods,
): TimeframeSelection {
  const { timeframe, date } = selection;

  if (timeframe === 'all') return { timeframe: 'all', date: null };
  if (!periods.earliest_date || !periods.latest_date || periods.years.length === 0) {
    return selection;
  }

  const earliestYear = periods.years[0];
  const latestYear = periods.years[periods.years.length - 1];

  if (timeframe === 'year') {
    const year = date ? parseInt(date.split('-')[0], 10) : latestYear;
    const snappedYear = Math.max(earliestYear, Math.min(latestYear, isNaN(year) ? latestYear : year));
    return { timeframe: 'year', date: `${snappedYear}-01-01` };
  }

  if (timeframe === 'month') {
    const earliest = periods.earliest_date.slice(0, 7);
    const latest = periods.latest_date.slice(0, 7);
    const requested = date ? date.slice(0, 7) : latest;
    const clamped =
      requested < earliest ? earliest : requested > latest ? latest : requested;
    return { timeframe: 'month', date: `${clamped}-01` };
  }

  return selection;
}

export function timeframeQuery(timeframe: string, date: string | null): string {
  const p = new URLSearchParams({ timeframe });
  if (date) p.set('date', date);
  return p.toString();
}
