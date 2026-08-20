import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
      expect(screen.getByText("全部摄像头")).toBeInTheDocument();
    });
    expect(screen.getByText("全部类型")).toBeInTheDocument();
    expect(screen.getByText("全部状态")).toBeInTheDocument();
    expect(screen.getByText("全部判定")).toBeInTheDocument();
    expect(screen.queryByText("__all__")).not.toBeInTheDocument();
  });
});
