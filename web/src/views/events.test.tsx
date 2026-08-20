import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { EventsPage } from "./events";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function jsonResp(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  };
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("EventsPage filters", () => {
  it("shows Chinese labels instead of the internal __all__ sentinel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/cameras")) return jsonResp([]);
        if (url.startsWith("/api/events")) return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<EventsPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "摄像头：全部摄像头" })).toBeInTheDocument();
    });
    expect(screen.getByRole("combobox", { name: "类型：全部类型" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "状态：全部状态" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "VLM 判定：全部判定" })).toBeInTheDocument();
    expect(screen.queryByText("__all__")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重置筛选" })).toBeDisabled();
  });

  it("resets all filters back to their defaults", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/cameras")) {
          return jsonResp([{ id: 1, name: "演示摄像头" }]);
        }
        if (url.startsWith("/api/events")) return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<EventsPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "类型：全部类型" })).toBeInTheDocument();
    });
    const starred = screen.getByRole("checkbox", { name: "仅看关注" });
    fireEvent.click(starred);

    const reset = screen.getByRole("button", { name: "重置筛选" });
    await waitFor(() => expect(reset).not.toBeDisabled());
    expect(starred).toBeChecked();

    fireEvent.click(reset);

    await waitFor(() => {
      expect(screen.getByRole("combobox", { name: "类型：全部类型" })).toBeInTheDocument();
    });
    expect(starred).not.toBeChecked();
    expect(reset).toBeDisabled();
  });
});
