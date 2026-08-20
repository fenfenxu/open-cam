import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nextNav } from "@/test/next-nav";
import { MarketplacePage } from "./marketplace";
import { SettingsPage } from "./settings";
import { TrainingPage } from "./training";

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

describe("TrainingPage", () => {
  it("keeps 说需求 as the first wizard step", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/cameras") return jsonResp([]);
        if (url === "/videos") return jsonResp([]);
        if (url === "/training/tasks") return jsonResp([]);
        if (url === "/api/system/vlm") return jsonResp({ configured: true });
        return jsonResp({ detail: url }, 404);
      }),
    );

    nextNav.pathname = "/training";
    render(<TrainingPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /说需求/ })).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: /生成任务定义/ })).toBeInTheDocument();
  });
});

describe("MarketplacePage", () => {
  it("renders pack cards and install form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/packs") {
          return jsonResp([
            {
              id: "fast-food",
              name: "快餐店",
              origin: "builtin",
              vertical: "餐饮",
              version: "1.0",
              author: "open-cam",
              description: "多路摄像头方案",
              cameras: [{ id: "counter", name: "柜台" }],
              rules: [{ name: "排队", camera: "counter" }],
            },
          ]);
        }
        if (url === "/cameras") return jsonResp([]);
        if (url === "/api/packs/online") return jsonResp({ note: "在线目录暂不可用" });
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<MarketplacePage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("快餐店")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("安装源")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "应用" })).toBeInTheDocument();
  });
});

describe("SettingsPage", () => {
  it("shows system info and empty notify table", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/system/info") {
          return jsonResp({
            version: "0.1.0",
            device: "cpu",
            device_config: "auto",
            memory_total_gb: 16,
            vram_total_gb: null,
            detector: "yolo",
            yolo_model: "n",
            detect_fps: 2,
            packs_available: 1,
            packs_installed: 0,
            data_dir: "/data",
          });
        }
        if (url === "/api/system/vlm") {
          return jsonResp({ configured: false, base_url: "", model: "" });
        }
        if (url === "/api/account/status") {
          return jsonResp({ logged_in: false, note: "本地模式" });
        }
        if (url === "/api/notify-channels") return jsonResp([]);
        if (url === "/cameras") return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<SettingsPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("0.1.0")).toBeInTheDocument();
    });
    expect(screen.getByText("还没有通知渠道。")).toBeInTheDocument();
    expect(screen.getByText("未登录")).toBeInTheDocument();
  });
});
