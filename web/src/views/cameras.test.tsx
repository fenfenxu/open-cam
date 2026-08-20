import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
        if (url.startsWith("/api/cameras")) return jsonResp([]);
        if (url.startsWith("/api/videos")) return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<CamerasPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/请新建/)).toBeInTheDocument();
    });
  });

  it("keeps complete Chinese and English names while typing and saves them", async () => {
    const requests: Array<{ url: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        requests.push({ url, body: init?.body?.toString() });
        if (url === "/api/cameras") {
          return jsonResp([
            { id: 1, name: "门口", source_type: "file", source_uri: "/a.mp4", status: "stopped" },
          ]);
        }
        if (url === "/api/videos") return jsonResp([]);
        if (url === "/api/cameras/1" && init?.method === "PUT") return jsonResp({});
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<CamerasPage />, { wrapper });

    const input = await screen.findByRole("textbox", { name: "摄像头 1 名称" });
    fireEvent.input(input, { target: { value: "新" } });
    fireEvent.input(input, { target: { value: "新门" } });
    fireEvent.input(input, { target: { value: "新门A" } });
    fireEvent.input(input, { target: { value: "新门AB" } });

    expect(input).toHaveValue("新门AB");
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(requests).toContainEqual({
      url: "/api/cameras/1",
        body: JSON.stringify({ name: "新门AB" }),
      });
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
        if (url === "/api/cameras/7") {
          return jsonResp({
            id: 7,
            name: "后门",
            source_type: "rtsp",
            source_uri: "rtsp://127.0.0.1:8554/x",
            status: "stopped",
          });
        }
        if (url === "/api/models") return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<CameraDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("该源为直播流，不支持回放。")).toBeInTheDocument();
    });
  });
});
