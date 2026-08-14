const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function parseJsonResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    const message =
      body?.error?.message ?? body?.detail ?? `Request failed (${res.status})`;
    throw new Error(message);
  }
  return res.json();
}

/**
 * Generic fetcher for SWR. Accepts a path relative to the API root
 * (e.g. "/api/top/songs?timeframe=month&date=2025-01-01").
 */
export async function fetcher<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  return parseJsonResponse<T>(res);
}

/**
 * Fetcher for same-origin Next.js route handlers (e.g. "/api/dashboard").
 * Unlike `fetcher`, this does not prefix the path with the backend API URL.
 */
export async function localFetcher<T>(path: string): Promise<T> {
  const res = await fetch(path);
  return parseJsonResponse<T>(res);
}
