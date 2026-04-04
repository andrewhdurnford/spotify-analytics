import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, it, expect, vi } from "vitest";
import Dropdown from "../Dropdown";

afterEach(() => {
  cleanup();
});

function renderDropdown(overrides = {}) {
  const props = {
    options: ["Option A", "Option B", "Option C"],
    selected: "Option A",
    onSelect: vi.fn(),
    label: "test-dropdown",
    ...overrides,
  };
  render(<Dropdown {...props} />);
  return props;
}

describe("Dropdown", () => {
  it("renders the selected option as button text", () => {
    renderDropdown();
    expect(
      screen.getByRole("button", { name: "test-dropdown" }),
    ).toHaveTextContent("Option A");
  });

  it("opens the listbox on click", async () => {
    const user = userEvent.setup();
    renderDropdown();

    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "test-dropdown" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();
  });

  it("closes the listbox on Escape", async () => {
    const user = userEvent.setup();
    renderDropdown();

    await user.click(screen.getByRole("button", { name: "test-dropdown" }));
    expect(screen.getByRole("listbox")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("calls onSelect when an option is clicked", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderDropdown();

    await user.click(screen.getByRole("button", { name: "test-dropdown" }));
    await user.click(screen.getByRole("option", { name: "Option B" }));

    expect(onSelect).toHaveBeenCalledWith("Option B");
  });

  it("navigates with ArrowDown and selects with Enter", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderDropdown();

    await user.click(screen.getByRole("button", { name: "test-dropdown" }));
    // Active index starts at 0 (selected = "Option A")
    await user.keyboard("{ArrowDown}"); // moves to Option B
    await user.keyboard("{Enter}");

    expect(onSelect).toHaveBeenCalledWith("Option B");
  });

  it("navigates with ArrowUp and wraps around", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderDropdown();

    await user.click(screen.getByRole("button", { name: "test-dropdown" }));
    // Active starts at 0, ArrowUp wraps to last (index 2 = Option C)
    await user.keyboard("{ArrowUp}");
    await user.keyboard("{Enter}");

    expect(onSelect).toHaveBeenCalledWith("Option C");
  });

  it("Home goes to first option, End to last", async () => {
    const user = userEvent.setup();
    const { onSelect } = renderDropdown({ selected: "Option B" });

    await user.click(screen.getByRole("button", { name: "test-dropdown" }));

    await user.keyboard("{End}");
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith("Option C");
  });

  it("sets aria-selected on the selected option", async () => {
    const user = userEvent.setup();
    renderDropdown({ selected: "Option B" });

    await user.click(screen.getByRole("button", { name: "test-dropdown" }));
    const optionB = screen.getByRole("option", { name: "Option B" });
    expect(optionB).toHaveAttribute("aria-selected", "true");

    const optionA = screen.getByRole("option", { name: "Option A" });
    expect(optionA).toHaveAttribute("aria-selected", "false");
  });

  it("uses formatLabel to display custom labels", async () => {
    const user = userEvent.setup();
    renderDropdown({
      options: [1, 2, 3],
      selected: 2,
      formatLabel: (v: number) => `Year ${v}`,
    });

    expect(
      screen.getByRole("button", { name: "test-dropdown" }),
    ).toHaveTextContent("Year 2");

    await user.click(screen.getByRole("button", { name: "test-dropdown" }));
    expect(screen.getByRole("option", { name: "Year 1" })).toBeInTheDocument();
  });
});
