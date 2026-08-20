import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { formatConsoleArgs, shouldInstallRuntimeOverlay } from "@/lib/runtime-overlay";
import { RuntimeOverlay } from "./runtime-overlay";

afterEach(() => {
  cleanup();
  vi.unstubAllEnvs();
});

describe("formatConsoleArgs", () => {
  it("joins api-style console.error arguments", () => {
    expect(formatConsoleArgs(["[api]", "<- 500 /cameras", { message: "boom" }])).toContain(
      "500 /cameras",
    );
    expect(formatConsoleArgs(["[api]", "<- 500 /cameras", { message: "boom" }])).toContain("boom");
  });
});

describe("shouldInstallRuntimeOverlay", () => {
  it("is off during next dev so the framework overlay stays unique", () => {
    vi.stubEnv("NODE_ENV", "development");
    expect(shouldInstallRuntimeOverlay()).toBe(false);
  });

  it("is on for the static export served by FastAPI", () => {
    vi.stubEnv("NODE_ENV", "production");
    expect(shouldInstallRuntimeOverlay()).toBe(true);
  });
});

describe("RuntimeOverlay", () => {
  it("shows a capsule after console.error when enabled", () => {
    render(<RuntimeOverlay enabled />);
    expect(screen.queryByRole("button", { name: /报错/ })).toBeNull();
    act(() => {
      console.error("[api]", "<- 500 /cameras", { message: "boom" });
    });
    expect(screen.getByRole("button", { name: /报错 1/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /报错 1/ }));
    expect(screen.getByText(/500 \/cameras/)).toBeInTheDocument();
  });

  it("does not trap errors when disabled", () => {
    render(<RuntimeOverlay enabled={false} />);
    console.error("should-not-surface");
    expect(screen.queryByRole("button", { name: /报错/ })).toBeNull();
  });
});
