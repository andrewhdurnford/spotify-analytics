/**
 * Backend fetch with failover.
 *
 * The primary backend runs on a home server that is sometimes offline; the
 * fallback (Render + Supabase) is a standby kept in sync with it. Requests go
 * to the primary first and retry against the fallback when the primary is
 * unreachable. When the primary fails, it is skipped for a short cooldown so
 * every request in a burst (the dashboard fans out six) doesn't pay for the
 * failed attempt.
 *
 * Works on both sides: on the server `API_URL`/`API_FALLBACK_URL` win, in the
 * browser only the `NEXT_PUBLIC_*` variants are defined.
 */

// Statuses that mean "the primary itself is down", not "the request was bad":
// gateway errors, plus Cloudflare's 52x range (530 = tunnel offline).
const FAILOVER_STATUSES = new Set([502, 503, 504, 520, 521, 522, 523, 524, 525, 526, 530]);

// Matches the backend's DB statement timeout: a healthy primary answers well
// inside this, so hitting it means the primary is hung.
const PRIMARY_TIMEOUT_MS = 10_000;
const PRIMARY_COOLDOWN_MS = 30_000;

interface ApiFetchOptions {
  primaryUrl: string;
  fallbackUrl?: string;
  timeoutMs?: number;
  cooldownMs?: number;
}

export function createApiFetch({
  primaryUrl,
  fallbackUrl,
  timeoutMs = PRIMARY_TIMEOUT_MS,
  cooldownMs = PRIMARY_COOLDOWN_MS,
}: ApiFetchOptions) {
  let primaryDownUntil = 0;

  return async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
    if (!fallbackUrl) {
      return init ? fetch(`${primaryUrl}${path}`, init) : fetch(`${primaryUrl}${path}`);
    }

    if (Date.now() >= primaryDownUntil) {
      try {
        const res = await fetch(`${primaryUrl}${path}`, {
          ...init,
          signal: init?.signal ?? AbortSignal.timeout(timeoutMs),
        });
        if (!FAILOVER_STATUSES.has(res.status)) {
          return res;
        }
      } catch (err) {
        // A caller-initiated abort isn't an outage; don't mask it.
        if (init?.signal?.aborted) throw err;
      }
      primaryDownUntil = Date.now() + cooldownMs;
    }

    return init ? fetch(`${fallbackUrl}${path}`, init) : fetch(`${fallbackUrl}${path}`);
  };
}

export const apiFetch = createApiFetch({
  primaryUrl: process.env.API_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  fallbackUrl: process.env.API_FALLBACK_URL || process.env.NEXT_PUBLIC_API_FALLBACK_URL || undefined,
});
