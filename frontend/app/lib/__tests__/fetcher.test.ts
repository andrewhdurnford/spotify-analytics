import { describe, it, expect, vi, beforeEach } from "vitest";
import { fetcher } from "../fetcher";

const API_URL = "http://localhost:8000";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("fetcher", () => {
  it("returns parsed JSON on success", async () => {
    const data = [{ track_name: "Song A", play_count: 5 }];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(data),
      }),
    );

    const result = await fetcher("/api/top/songs");
    expect(result).toEqual(data);
    expect(fetch).toHaveBeenCalledWith(`${API_URL}/api/top/songs`);
  });

  it("throws with error.message from structured API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 400,
        json: () =>
          Promise.resolve({
            error: { code: "INVALID_DATE", message: "Invalid date format" },
          }),
      }),
    );

    await expect(fetcher("/api/top/songs?date=bad")).rejects.toThrow(
      "Invalid date format",
    );
  });

  it("throws with detail from FastAPI validation error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: () => Promise.resolve({ detail: "Validation error" }),
      }),
    );

    await expect(fetcher("/api/top/songs")).rejects.toThrow(
      "Validation error",
    );
  });

  it("throws with status code when body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error("not json")),
      }),
    );

    await expect(fetcher("/api/top/songs")).rejects.toThrow(
      "Request failed (500)",
    );
  });
});
