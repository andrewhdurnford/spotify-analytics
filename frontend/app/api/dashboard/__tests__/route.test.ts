import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DASHBOARD_CACHE_CONTROL, DASHBOARD_NO_STORE_CACHE_CONTROL } from "../cache";
import { GET } from "../route";

const API_URL = "http://localhost:8000";

function jsonResponse(body: unknown, ok = true, status = ok ? 200 : 500) {
  return {
    ok,
    status,
    json: () => Promise.resolve(body),
  } as Response;
}

function makeRequest(query = ""): Request {
  return new Request(`http://localhost:3000/api/dashboard${query}`);
}

const SONGS = [{ track_name: "A", artist_name: "Artist", track_id: "1", artist_id: null, album_image: null, play_count: 5 }];
const ARTISTS = [{ artist_name: "Artist", spotify_id: "a1", image_url: null, play_count: 5 }];
const ALBUMS = [{ album_name: "Album", artist_name: "Artist", artist_id: "a1", album_image: null, play_count: 5 }];
const STATS = {
  total_play_count: 5,
  total_listening_ms: 100_000,
  unique_songs: 1,
  unique_artists: 1,
  unique_albums: 1,
  active_days: 1,
};
const TIMELINE = { timeline: [{ period: "2025-01", play_count: 5 }], granularity: "monthly" };
const PERIODS = { years: [2025], earliest_date: "2025-01-01", latest_date: "2025-06-01" };

function mockAllUpstreamsSucceed() {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/api/top/songs")) return Promise.resolve(jsonResponse(SONGS));
      if (url.includes("/api/top/artists")) return Promise.resolve(jsonResponse(ARTISTS));
      if (url.includes("/api/top/albums")) return Promise.resolve(jsonResponse(ALBUMS));
      if (url.includes("/api/top/stats")) return Promise.resolve(jsonResponse(STATS));
      if (url.includes("/api/top/timeline")) return Promise.resolve(jsonResponse(TIMELINE));
      if (url.includes("/api/top/available-periods")) return Promise.resolve(jsonResponse(PERIODS));
      throw new Error(`Unexpected URL: ${url}`);
    }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GET /api/dashboard", () => {
  it("combines songs, artists, albums, stats, timeline and periods into one response", async () => {
    mockAllUpstreamsSucceed();

    const res = await GET(makeRequest("?timeframe=month&date=2025-03-01"));
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body).toEqual({
      songs: SONGS,
      artists: ARTISTS,
      albums: ALBUMS,
      stats: STATS,
      timeline: TIMELINE,
      periods: PERIODS,
      errors: [],
    });
  });

  it("forwards timeframe/date query params to each per-timeframe upstream call", async () => {
    mockAllUpstreamsSucceed();

    await GET(makeRequest("?timeframe=year&date=2024-01-01"));

    const fetchMock = fetch as unknown as ReturnType<typeof vi.fn>;
    const calledUrls = fetchMock.mock.calls.map((call) => call[0] as string);

    expect(calledUrls).toContain(`${API_URL}/api/top/songs?timeframe=year&date=2024-01-01`);
    expect(calledUrls).toContain(`${API_URL}/api/top/artists?timeframe=year&date=2024-01-01`);
    expect(calledUrls).toContain(`${API_URL}/api/top/albums?timeframe=year&date=2024-01-01`);
    expect(calledUrls).toContain(`${API_URL}/api/top/stats?timeframe=year&date=2024-01-01`);
    expect(calledUrls).toContain(`${API_URL}/api/top/timeline?timeframe=year&date=2024-01-01`);
    // available-periods is timeframe-independent and should not carry the query string.
    expect(calledUrls).toContain(`${API_URL}/api/top/available-periods`);
  });

  it("sets a five-minute stale-while-revalidate Cache-Control header on success", async () => {
    mockAllUpstreamsSucceed();

    const res = await GET(makeRequest());

    expect(res.headers.get("Cache-Control")).toBe(DASHBOARD_CACHE_CONTROL);
    expect(DASHBOARD_CACHE_CONTROL).toContain("s-maxage=300");
    expect(DASHBOARD_CACHE_CONTROL).toContain("stale-while-revalidate");
  });

  it("degrades gracefully when a single upstream section fails, without caching the response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.includes("/api/top/stats")) {
          return Promise.resolve(jsonResponse({ detail: "boom" }, false, 500));
        }
        if (url.includes("/api/top/songs")) return Promise.resolve(jsonResponse(SONGS));
        if (url.includes("/api/top/artists")) return Promise.resolve(jsonResponse(ARTISTS));
        if (url.includes("/api/top/albums")) return Promise.resolve(jsonResponse(ALBUMS));
        if (url.includes("/api/top/timeline")) return Promise.resolve(jsonResponse(TIMELINE));
        if (url.includes("/api/top/available-periods")) return Promise.resolve(jsonResponse(PERIODS));
        throw new Error(`Unexpected URL: ${url}`);
      }),
    );

    const res = await GET(makeRequest());
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.errors).toEqual(["stats"]);
    expect(body.stats).toBeNull();
    expect(body.songs).toEqual(SONGS);
    expect(res.headers.get("Cache-Control")).toBe(DASHBOARD_NO_STORE_CACHE_CONTROL);
  });

  it("degrades gracefully when an upstream fetch throws (network error)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.includes("/api/top/albums")) return Promise.reject(new Error("network down"));
        if (url.includes("/api/top/songs")) return Promise.resolve(jsonResponse(SONGS));
        if (url.includes("/api/top/artists")) return Promise.resolve(jsonResponse(ARTISTS));
        if (url.includes("/api/top/stats")) return Promise.resolve(jsonResponse(STATS));
        if (url.includes("/api/top/timeline")) return Promise.resolve(jsonResponse(TIMELINE));
        if (url.includes("/api/top/available-periods")) return Promise.resolve(jsonResponse(PERIODS));
        throw new Error(`Unexpected URL: ${url}`);
      }),
    );

    const res = await GET(makeRequest());
    const body = await res.json();

    expect(res.status).toBe(200);
    expect(body.errors).toEqual(["albums"]);
    expect(body.albums).toEqual([]);
  });

  it("returns a structured 502 error and does not cache when every upstream call fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(jsonResponse({ detail: "down" }, false, 503))),
    );

    const res = await GET(makeRequest());
    const body = await res.json();

    expect(res.status).toBe(502);
    expect(body.error.code).toBe("UPSTREAM_UNAVAILABLE");
    expect(res.headers.get("Cache-Control")).toBe(DASHBOARD_NO_STORE_CACHE_CONTROL);
  });
});
