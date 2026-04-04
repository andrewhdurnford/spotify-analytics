const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

/**
 * Generic fetcher for SWR. Accepts a path relative to the API root
 * (e.g. "/api/top/songs?timeframe=month&date=2025-01-01").
 */
export async function fetcher<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const message =
      body?.error?.message ?? body?.detail ?? `Request failed (${res.status})`;
    throw new Error(message);
  }
  return res.json();
}
