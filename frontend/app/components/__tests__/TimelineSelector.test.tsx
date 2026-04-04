import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, it, expect, vi } from "vitest";
import TimelineSelector from "../TimelineSelector";

afterEach(() => {
  cleanup();
});

function renderSelector(overrides = {}) {
  const props = {
    onTimeframeChange: vi.fn(),
    availableYears: [2023, 2024, 2025],
    earliestDate: "2023-03-15",
    latestDate: "2025-06-10",
    selectedTimeframe: "all",
    selectedDate: null as string | null,
    ...overrides,
  };
  render(<TimelineSelector {...props} />);
  return props;
}

describe("TimelineSelector", () => {
  it("renders 'View By' dropdown with All Time selected", () => {
    renderSelector();
    expect(
      screen.getByRole("button", { name: "View by timeframe" }),
    ).toHaveTextContent("All Time");
  });

  it("does not show Year or Month dropdowns when 'all' is selected", () => {
    renderSelector();
    expect(screen.queryByRole("button", { name: "Select year" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Select month" })).not.toBeInTheDocument();
  });

  it("shows Year dropdown when timeframe is 'year'", () => {
    renderSelector({ selectedTimeframe: "year", selectedDate: "2025-01-01" });
    expect(screen.getByRole("button", { name: "Select year" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Select month" })).not.toBeInTheDocument();
  });

  it("shows both Year and Month dropdowns when timeframe is 'month'", () => {
    renderSelector({ selectedTimeframe: "month", selectedDate: "2025-03-01" });
    expect(screen.getByRole("button", { name: "Select year" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Select month" })).toBeInTheDocument();
  });

  it("calls onTimeframeChange('all', null) when switching to All Time", async () => {
    const user = userEvent.setup();
    const { onTimeframeChange } = renderSelector({
      selectedTimeframe: "year",
      selectedDate: "2025-01-01",
    });

    await user.click(screen.getByRole("button", { name: "View by timeframe" }));
    await user.click(screen.getByRole("option", { name: "All Time" }));

    expect(onTimeframeChange).toHaveBeenCalledWith("all", null);
  });

  it("calls onTimeframeChange with date when switching to Year", async () => {
    const user = userEvent.setup();
    const { onTimeframeChange } = renderSelector();

    await user.click(screen.getByRole("button", { name: "View by timeframe" }));
    await user.click(screen.getByRole("option", { name: "Year" }));

    // Should use the last available year (2025) with month=01
    expect(onTimeframeChange).toHaveBeenCalledWith("year", "2025-01-01");
  });

  it("calls onTimeframeChange with date when switching to Month", async () => {
    const user = userEvent.setup();
    const { onTimeframeChange } = renderSelector();

    await user.click(screen.getByRole("button", { name: "View by timeframe" }));
    await user.click(screen.getByRole("option", { name: "Month" }));

    // Should use last available year (2025) and default month=01
    expect(onTimeframeChange).toHaveBeenCalledWith("month", "2025-01-01");
  });

  it("changing year calls onTimeframeChange with updated date", async () => {
    const user = userEvent.setup();
    const { onTimeframeChange } = renderSelector({
      selectedTimeframe: "year",
      selectedDate: "2025-01-01",
    });

    await user.click(screen.getByRole("button", { name: "Select year" }));
    await user.click(screen.getByRole("option", { name: "2024" }));

    expect(onTimeframeChange).toHaveBeenCalledWith("year", "2024-01-01");
  });

  it("clamps month to available range when changing year", async () => {
    const user = userEvent.setup();
    // Currently on 2024 January (month index 0) in month view
    // Earliest is 2023-03, so switching to 2023 should clamp to March (index 2)
    const { onTimeframeChange } = renderSelector({
      selectedTimeframe: "month",
      selectedDate: "2024-01-01",
    });

    await user.click(screen.getByRole("button", { name: "Select year" }));
    await user.click(screen.getByRole("option", { name: "2023" }));

    // January (index 0) clamped to March (index 2) for 2023
    expect(onTimeframeChange).toHaveBeenCalledWith("month", "2023-03-01");
  });

  it("selecting a month calls onTimeframeChange correctly", async () => {
    const user = userEvent.setup();
    const { onTimeframeChange } = renderSelector({
      selectedTimeframe: "month",
      selectedDate: "2025-03-01",
    });

    await user.click(screen.getByRole("button", { name: "Select month" }));
    await user.click(screen.getByRole("option", { name: "April" }));

    expect(onTimeframeChange).toHaveBeenCalledWith("month", "2025-04-01");
  });

  it("filters months based on earliest/latest date for boundary years", async () => {
    const user = userEvent.setup();
    // 2023 earliest is March, so January and February should not appear
    renderSelector({
      selectedTimeframe: "month",
      selectedDate: "2023-03-01",
    });

    await user.click(screen.getByRole("button", { name: "Select month" }));

    expect(screen.queryByRole("option", { name: "January" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "February" })).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "March" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "December" })).toBeInTheDocument();
  });

  it("filters months on the latest year boundary", async () => {
    const user = userEvent.setup();
    // 2025 latest is June
    renderSelector({
      selectedTimeframe: "month",
      selectedDate: "2025-03-01",
    });

    await user.click(screen.getByRole("button", { name: "Select month" }));

    expect(screen.getByRole("option", { name: "January" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "June" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "July" })).not.toBeInTheDocument();
  });
});
