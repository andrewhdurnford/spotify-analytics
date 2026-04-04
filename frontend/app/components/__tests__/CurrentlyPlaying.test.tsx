import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, it, expect, vi } from "vitest";
import CurrentlyPlaying from "../CurrentlyPlaying";

afterEach(() => {
  cleanup();
});

// Mock next/image to render a plain <img>
vi.mock("next/image", () => ({
  default: (props: Record<string, unknown>) => {
    // eslint-disable-next-line @next/next/no-img-element, jsx-a11y/alt-text
    return <img {...props} />;
  },
}));

// Mock SWR to control the returned data
const mockUseSWR = vi.fn();
vi.mock("swr", () => ({
  default: (...args: unknown[]) => mockUseSWR(...args),
}));

describe("CurrentlyPlaying", () => {
  it("renders nothing when no track is playing", () => {
    mockUseSWR.mockReturnValue({
      data: { is_playing: false },
      error: undefined,
      isLoading: false,
    });

    const { container } = render(<CurrentlyPlaying />);
    expect(container.querySelector(".flex.items-center.gap-2")).not.toBeInTheDocument();
    expect(screen.queryByText("NOW PLAYING")).not.toBeInTheDocument();
  });

  it("renders nothing when data is undefined", () => {
    mockUseSWR.mockReturnValue({
      data: undefined,
      error: undefined,
      isLoading: true,
    });

    render(<CurrentlyPlaying />);
    expect(screen.queryByText("NOW PLAYING")).not.toBeInTheDocument();
  });

  it("renders track info when playing", () => {
    mockUseSWR.mockReturnValue({
      data: {
        is_playing: true,
        track_name: "Test Song",
        artist_name: "Test Artist",
        album_name: "Test Album",
        album_image: "https://img/test.jpg",
        progress_ms: 60_000,
        duration_ms: 200_000,
      },
      error: undefined,
      isLoading: false,
    });

    render(<CurrentlyPlaying />);
    expect(screen.getByText("NOW PLAYING")).toBeInTheDocument();
    expect(screen.getByText("Test Song")).toBeInTheDocument();
    expect(screen.getByText("Test Artist")).toBeInTheDocument();
    expect(screen.getByAltText("Currently playing")).toHaveAttribute(
      "src",
      "https://img/test.jpg",
    );
  });

  it("renders track without image when album_image is null", () => {
    mockUseSWR.mockReturnValue({
      data: {
        is_playing: true,
        track_name: "No Image Song",
        artist_name: "Some Artist",
        album_image: null,
      },
      error: undefined,
      isLoading: false,
    });

    render(<CurrentlyPlaying />);
    expect(screen.getByText("No Image Song")).toBeInTheDocument();
    expect(screen.queryByAltText("Currently playing")).not.toBeInTheDocument();
  });
});
