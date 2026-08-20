import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MarketplacePage } from "./marketplace";

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

const fastFoodCard = {
  id: "fast-food",
  name: "快餐店",
  version: "1.1.0",
  vertical: "餐饮-快餐",
  author: "open-cam",
  origin: "builtin",
  fingerprint: "fp1",
  tagline: "看清门口客流、点餐排队与闭店后的安全问题",
  description: "面向快餐门店",
  availability: "available",
  unavailable_reason: null,
  camera_count: 4,
  rule_count: 4,
  scene_count: 4,
  has_demo: true,
  trial_available: true,
  application_mode: "create_cameras",
  cover_asset_id: "a_cover",
};

const brokenCard = {
  id: "broken-pack",
  name: "broken-pack",
  version: "0.0.0",
  vertical: "",
  author: "",
  origin: "installed",
  fingerprint: "fp2",
  tagline: "",
  description: "",
  availability: "unavailable",
  unavailable_reason: "pack.yaml 解析失败",
  camera_count: 0,
  rule_count: 0,
  scene_count: 0,
  has_demo: false,
  trial_available: false,
  application_mode: "existing_camera",
  cover_asset_id: null,
};

function stubFetch(cards: unknown[]) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/packs?view=cards")) return jsonResp(cards);
      if (url.startsWith("/api/packs/online")) return jsonResp({ note: "" });
      return jsonResp({ detail: url }, 404);
    }),
  );
}

describe("MarketplacePage", () => {
  it("卡片展示业务价值、机位与体验状态，主入口为查看详情", async () => {
    stubFetch([fastFoodCard]);
    render(<MarketplacePage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("快餐店")).toBeInTheDocument();
    });
    expect(screen.getByText(/看清门口客流/)).toBeInTheDocument();
    expect(screen.getByText(/4 路机位 · 4 条规则 · 4 个场景/)).toBeInTheDocument();
    expect(screen.getByText("效果演示 · 可试跑")).toBeInTheDocument();

    const link = screen.getByRole("link", { name: "查看详情" });
    expect(link).toHaveAttribute("href", "/marketplace/fast-food");
    // 卡片不再直接应用
    expect(screen.queryByRole("button", { name: "应用" })).not.toBeInTheDocument();
  });

  it("无效包不静默消失：展示不可用状态与原因，仍可进入详情", async () => {
    stubFetch([brokenCard]);
    render(<MarketplacePage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("broken-pack")).toBeInTheDocument();
    });
    expect(screen.getByText("不可用")).toBeInTheDocument();
    expect(screen.getByText("pack.yaml 解析失败")).toBeInTheDocument();
    expect(screen.getByText("不可体验")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: "查看详情" });
    expect(link).toHaveAttribute("href", "/marketplace/broken-pack");
  });
});
