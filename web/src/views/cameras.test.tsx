import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nextNav } from "@/test/next-nav";
import { CameraDetailPage } from "./camera-detail";
import { CamerasPage } from "./cameras";

afterEach(() => {
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

describe("CamerasPage", () => {
  it("renders 请新建 when the camera list is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/cameras")) return jsonResp([]);
        if (url.startsWith("/videos")) return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<CamerasPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/请新建/)).toBeInTheDocument();
    });
  });
});

describe("CameraDetailPage", () => {
  it("shows RTSP replay copy", async () => {
    nextNav.pathname = "/cameras/7";
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/cameras/7") {
          return jsonResp({
            id: 7,
            name: "后门",
            source_type: "rtsp",
            source_uri: "rtsp://127.0.0.1:8554/x",
            status: "stopped",
          });
        }
        if (url === "/models") return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<CameraDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("该源为直播流，不支持回放。")).toBeInTheDocument();
    });
  });
});
