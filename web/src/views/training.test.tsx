import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { nextNav } from "@/test/next-nav";
import { MarketplacePage } from "./marketplace";
import { SettingsPage } from "./settings";
import { TrainingPage } from "./training";

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

describe("TrainingPage", () => {
  it("keeps 说需求 as the first wizard step", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/cameras") return jsonResp([]);
        if (url === "/api/videos") return jsonResp([]);
        if (url === "/api/training/tasks") return jsonResp([]);
        if (url === "/api/system/vlm") return jsonResp({ configured: true });
        return jsonResp({ detail: url }, 404);
      }),
    );

    nextNav.pathname = "/training";
    render(<TrainingPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: /说需求/ })).toBeInTheDocument();
    });
    expect(screen.getByRole("navigation", { name: "训练步骤" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "第 1 步：说需求" })).toHaveAttribute("aria-current", "step");
    expect(screen.getByRole("button", { name: "确认定义" })).toBeDisabled();
    expect(screen.getByRole("button", { name: /生成任务定义/ })).toBeInTheDocument();
  });
});

describe("MarketplacePage", () => {
  it("renders pack cards and install form", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/packs?view=cards")) {
          return jsonResp([
            {
              id: "fast-food",
              name: "快餐店",
              origin: "builtin",
              vertical: "餐饮",
              version: "1.0",
              author: "open-cam",
              fingerprint: "fp",
              tagline: "多路摄像头方案",
              description: "多路摄像头方案",
              availability: "available",
              unavailable_reason: null,
              camera_count: 1,
              rule_count: 1,
              scene_count: 1,
              has_demo: true,
              trial_available: false,
              application_mode: "create_cameras",
              cover_asset_id: null,
            },
          ]);
        }
        if (url === "/api/cameras") return jsonResp([]);
        if (url === "/api/packs/online") return jsonResp({ note: "在线目录暂不可用" });
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<MarketplacePage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("快餐店")).toBeInTheDocument();
    });
    expect(screen.getByLabelText("安装源")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择方案包文件" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "查看详情" })).toHaveAttribute(
      "href",
      "/marketplace/fast-food",
    );
  });

  it("uploads a selected zip file when installing a local pack", async () => {
    const requests: Array<{ url: string; body?: BodyInit | null }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        requests.push({ url, body: init?.body });
        if (url.startsWith("/api/packs?view=cards")) return jsonResp([]);
        if (url === "/api/cameras") return jsonResp([]);
        if (url === "/api/packs/online") return jsonResp({ note: "在线目录暂不可用" });
        if (url === "/api/packs/install-upload") {
          return jsonResp({
            id: "custom",
            name: "自定义方案",
            origin: "installed",
            vertical: "通用",
            version: "1.0",
            description: "本地方案包",
          });
        }
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<MarketplacePage />, { wrapper });

    const file = new File(["zip"], "custom.zip", { type: "application/zip" });
    const fileInput = screen.getAllByLabelText("选择方案包文件").at(-1);
    expect(fileInput).toBeDefined();
    fireEvent.change(fileInput!, { target: { files: [file] } });
    expect(screen.getByText("已选择：custom.zip")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "安装" }).at(-1)!);

    await waitFor(() => {
      const request = requests.find((item) => item.url === "/api/packs/install-upload");
      expect(request?.body).toBeInstanceOf(FormData);
    });
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
        if (url === "/api/cameras") return jsonResp([]);
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
