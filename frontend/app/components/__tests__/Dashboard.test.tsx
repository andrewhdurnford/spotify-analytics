import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Dashboard from "../Dashboard";

afterEach(() => {
  cleanup();
});

const mockReplace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}));

const mockUseSWR = vi.fn();
vi.mock("swr", () => ({
  default: (...args: unknown[]) => mockUseSWR(...args),
}));

vi.mock("../TimelineSelector", () => ({
  default: () => <div data-testid="timeline-selector" />,
}));
vi.mock("../CurrentlyPlaying", () => ({
  default: () => <div data-testid="currently-playing" />,
}));
vi.mock("../ListeningStats", () => ({
  default: ({ stats }: { stats?: { total_play_count: number } }) => (
    <div data-testid="listening-stats">{stats?.total_play_count ?? "none"}</div>
  ),
}));
vi.mock("../ListeningTimeline", () => ({
  default: ({ data }: { data: unknown[] }) => (
    <div data-testid="listening-timeline">{data.length}</div>
  ),
}));
vi.mock("../MediaSection", () => ({
  default: ({ title, items }: { title: string; items: unknown[] }) => (
    <div data-testid={`media-${title}`}>{items.length}</div>
  ),
}));
vi.mock("../RecentlyPlayedSection", () => ({
  default: ({ items }: { items: unknown[] }) => (
    <div data-testid="recently-played">{items.length}</div>
  ),
}));

interface SwrState {
  data: unknown;
  error: unknown;
  isLoading: boolean;
}

const LOADING: SwrState = { data: undefined, error: undefined, isLoading: true };

function setupSWR(overrides: { dashboard?: SwrState; recentlyPlayed?: SwrState } = {}) {
  mockUseSWR.mockImplementation((key: unknown) => {
    if (typeof key === "string" && key.startsWith("/api/dashboard")) {
      return overrides.dashboard ?? LOADING;
    }
    if (typeof key === "string" && key.startsWith("/api/top/recently-played")) {
      return overrides.recentlyPlayed ?? LOADING;
    }
    return LOADING;
  });
}

describe("Dashboard", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    mockUseSWR.mockReset();
  });

  it("requests the combined /api/dashboard endpoint with timeframe/date params, not per-section endpoints", () => {
    setupSWR();
    render(<Dashboard initialTimeframe="month" initialDate="2025-03-01" />);

    const calledKeys = mockUseSWR.mock.calls.map((call) => call[0]);
    expect(calledKeys).toContain("/api/dashboard?timeframe=month&date=2025-03-01");

    const backendTopCalls = calledKeys.filter(
      (key) =>
        typeof key === "string" &&
        key.startsWith("/api/top/") &&
        !key.startsWith("/api/top/recently-played"),
    );
    expect(backendTopCalls).toEqual([]);
  });

  it("keeps recently-played on its own independent 5-minute poll", () => {
    setupSWR();
    render(<Dashboard initialTimeframe="all" initialDate={null} />);

    const recentlyPlayedCall = mockUseSWR.mock.calls.find(
      (call) => typeof call[0] === "string" && call[0].startsWith("/api/top/recently-played"),
    );
    expect(recentlyPlayedCall).toBeTruthy();
    expect(recentlyPlayedCall?.[2]).toMatchObject({ refreshInterval: 5 * 60_000 });
  });

  it("shows loading skeletons while the combined dashboard request is in flight", () => {
    setupSWR({ dashboard: LOADING });
    render(<Dashboard initialTimeframe="all" initialDate={null} />);

    expect(screen.getByLabelText("Loading Period Stats")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading Top Songs")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading Top Albums")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading Top Artists")).toBeInTheDocument();
    expect(screen.getByLabelText("Loading Listening Over Time")).toBeInTheDocument();
  });

  it("renders every section from a single combined response once loaded", () => {
    setupSWR({
      dashboard: {
        data: {
          songs: [{ track_name: "A" }],
          artists: [{ artist_name: "B" }],
          albums: [{ album_name: "C" }],
          stats: {
            total_play_count: 5,
            total_listening_ms: 0,
            unique_songs: 1,
            unique_artists: 1,
            unique_albums: 1,
            active_days: 1,
          },
          timeline: { timeline: [{ period: "2025-01", play_count: 3 }], granularity: "monthly" },
          periods: { years: [2025], earliest_date: "2025-01-01", latest_date: "2025-06-01" },
          errors: [],
        },
        error: undefined,
        isLoading: false,
      },
      recentlyPlayed: {
        data: {
          items: [
            {
              track_name: "X",
              artist_name: "Y",
              track_id: null,
              artist_id: null,
              album_image: null,
              played_at: "2025-01-01T00:00:00",
            },
          ],
          page: 1,
          page_size: 10,
          has_next: false,
        },
        error: undefined,
        isLoading: false,
      },
    });

    render(<Dashboard initialTimeframe="all" initialDate={null} />);

    expect(screen.getByTestId("listening-stats")).toHaveTextContent("5");
    expect(screen.getByTestId("listening-timeline")).toHaveTextContent("1");
    expect(screen.getByTestId("media-Top Songs")).toHaveTextContent("1");
    expect(screen.getByTestId("media-Top Albums")).toHaveTextContent("1");
    expect(screen.getByTestId("media-Top Artists")).toHaveTextContent("1");
    expect(screen.getByTestId("recently-played")).toHaveTextContent("1");
    expect(screen.queryByText(/Failed to load data/i)).not.toBeInTheDocument();
  });

  it("shows an error banner when the combined dashboard request fails outright", () => {
    setupSWR({ dashboard: { data: undefined, error: new Error("boom"), isLoading: false } });
    render(<Dashboard initialTimeframe="all" initialDate={null} />);

    expect(screen.getByText(/Failed to load data/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Loading Listening Over Time")).not.toBeInTheDocument();
  });

  it("shows an error banner when the combined response reports partial upstream errors", () => {
    setupSWR({
      dashboard: {
        data: {
          songs: [],
          artists: [],
          albums: [],
          stats: null,
          timeline: null,
          periods: null,
          errors: ["stats"],
        },
        error: undefined,
        isLoading: false,
      },
    });

    render(<Dashboard initialTimeframe="all" initialDate={null} />);

    expect(screen.getByText(/Failed to load data/i)).toBeInTheDocument();
  });
});
