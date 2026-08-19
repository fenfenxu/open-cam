import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DashboardPage } from "./dashboard";

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
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter>{children}</MemoryRouter>
    </QueryClientProvider>
  );
}

describe("DashboardPage", () => {
  it("shows empty-state copy linking to cameras", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/cameras")) return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<DashboardPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/还没有摄像头/)).toBeInTheDocument();
    });
    const link = screen.getByRole("link", { name: /摄像头/ });
    expect(link).toHaveAttribute("href", "/cameras");
  });

  it("renders a running camera card that links to detail", async () => {
    const buckets = Array.from({ length: 24 }, (_, hour) => ({
      hour,
      in: hour === 9 ? 3 : 0,
      out: hour === 9 ? 1 : 0,
    }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/cameras") {
          return jsonResp([
            {
              id: 3,
              name: "门口",
              source_type: "file",
              source_uri: "/v/demo.mp4",
              status: "running",
            },
          ]);
        }
        if (url.startsWith("/events?camera_id=3")) {
          return jsonResp([{ ts: 1_700_000_000 }]);
        }
        if (url.startsWith("/api/stats/footfall?camera_id=3")) {
          return jsonResp({
            camera_id: 3,
            date: "2026-08-19",
            buckets,
            total_in: 3,
            total_out: 1,
          });
        }
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<DashboardPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/进 3 \/ 出 1/)).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /门口/ })).toHaveAttribute(
      "href",
      "/cameras/3",
    );
    expect(screen.getByLabelText("24 小时进/出客流")).toBeInTheDocument();
  });

  it("shows line-crossing hint when footfall is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/cameras") {
          return jsonResp([
            {
              id: 1,
              name: "仓库",
              source_type: "rtsp",
              source_uri: "rtsp://x",
              status: "stopped",
            },
          ]);
        }
        if (url.startsWith("/events")) return jsonResp([]);
        if (url.startsWith("/api/stats/footfall")) {
          return jsonResp({
            camera_id: 1,
            date: "2026-08-19",
            buckets: Array.from({ length: 24 }, (_, hour) => ({
              hour,
              in: 0,
              out: 0,
            })),
            total_in: 0,
            total_out: 0,
          });
        }
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<DashboardPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/先配置「越线计数」规则/)).toBeInTheDocument();
    });
  });
});
