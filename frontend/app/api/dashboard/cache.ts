/**
 * Vercel edge cache: five minutes fresh, then serve-stale-while-revalidating.
 * `public` is safe today because the app is single-user with no auth; if
 * per-user data or auth is ever added, this must become per-user (or private).
 */
export const DASHBOARD_CACHE_CONTROL = 'public, s-maxage=300, stale-while-revalidate=60';

/** Used whenever the response shouldn't be cached (any upstream failure). */
export const DASHBOARD_NO_STORE_CACHE_CONTROL = 'no-store';
