import { describe, it, expect, vi, beforeEach } from "vitest";
import { createApiFetch } from "../api";

const PRIMARY = "https://home.example.com";
const FALLBACK = "https://render.example.com";

function response(status: number) {
  return { ok: status < 400, status } as Response;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("createApiFetch", () => {
  it("uses only the primary when no fallback is configured", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(530)));
    const apiFetch = createApiFetch({ primaryUrl: PRIMARY });

    const res = await apiFetch("/api/top/songs");

    expect(res.status).toBe(530);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch).toHaveBeenCalledWith(`${PRIMARY}/api/top/songs`);
  });

  it("returns the primary response when it is up", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(200)));
    const apiFetch = createApiFetch({ primaryUrl: PRIMARY, fallbackUrl: FALLBACK });

    const res = await apiFetch("/api/top/songs");

    expect(res.status).toBe(200);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(`${PRIMARY}/api/top/songs`);
  });

  it("does not fail over on client errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response(404)));
    const apiFetch = createApiFetch({ primaryUrl: PRIMARY, fallbackUrl: FALLBACK });

    const res = await apiFetch("/api/tracks/missing");

    expect(res.status).toBe(404);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("fails over when the tunnel reports the primary offline", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce(response(530)).mockResolvedValueOnce(response(200)),
    );
    const apiFetch = createApiFetch({ primaryUrl: PRIMARY, fallbackUrl: FALLBACK });

    const res = await apiFetch("/api/top/songs", { cache: "no-store" });

    expect(res.status).toBe(200);
    expect(vi.mocked(fetch).mock.calls[1]).toEqual([
      `${FALLBACK}/api/top/songs`,
      { cache: "no-store" },
    ]);
  });

  it("fails over on network errors and skips the primary during the cooldown", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValueOnce(new TypeError("fetch failed")).mockResolvedValue(response(200)),
    );
    const apiFetch = createApiFetch({ primaryUrl: PRIMARY, fallbackUrl: FALLBACK });

    await apiFetch("/api/top/songs");
    await apiFetch("/api/top/artists");

    expect(vi.mocked(fetch).mock.calls.map((call) => call[0])).toEqual([
      `${PRIMARY}/api/top/songs`,
      `${FALLBACK}/api/top/songs`,
      `${FALLBACK}/api/top/artists`,
    ]);
  });

  it("retries the primary once the cooldown expires", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal(
        "fetch",
        vi.fn().mockRejectedValueOnce(new TypeError("fetch failed")).mockResolvedValue(response(200)),
      );
      const apiFetch = createApiFetch({
        primaryUrl: PRIMARY,
        fallbackUrl: FALLBACK,
        cooldownMs: 1_000,
      });

      await apiFetch("/api/top/songs");
      vi.advanceTimersByTime(1_001);
      await apiFetch("/api/top/songs");

      expect(vi.mocked(fetch).mock.calls[2][0]).toBe(`${PRIMARY}/api/top/songs`);
    } finally {
      vi.useRealTimers();
    }
  });
});
