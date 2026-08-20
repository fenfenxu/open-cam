import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextNav } from "@/test/next-nav";
import { PackDetailPage } from "./pack-detail";

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

const detail = {
  id: "fast-food",
  name: "快餐店",
  version: "1.1.0",
  vertical: "餐饮-快餐",
  author: "open-cam",
  origin: "builtin",
  fingerprint: "abcdef1234567890",
  description: "面向快餐门店",
  availability: "available",
  unavailable_reason: null,
  presentation: {
    tagline: "看清门口客流、点餐排队与闭店后的安全问题",
    cover_asset_id: "a_cover",
    outcomes: [{ title: "掌握进出店客流", description: "区分进店与出店方向" }],
    requirements: ["4 路摄像头：门口、点餐区、后厨、店内"],
    limitations: ["仅识别人形目标，不能识别身份"],
  },
  cameras: [
    {
      id: "door",
      name: "门口",
      purpose: "统计进出店客流的方向与数量",
      placement: "正对或斜对门口",
      poster_asset_id: "a_poster",
      rule_ids: ["r1"],
    },
  ],
  rules: [
    {
      id: "r1",
      name: "门口越线计数",
      type: "line_crossing",
      type_label: "越线计数",
      camera_id: "door",
      cooldown: 5,
      intent: "observe",
      summary: "越线计数 · 观察记账 · 冷却 5 秒",
    },
  ],
  experience: {
    scenes: [
      {
        id: "door-flow",
        camera_id: "door",
        title: "门口进出客流",
        available: true,
        degrade_reason: null,
        input_asset_id: "a_in",
        result_asset_id: "a_out",
        poster_asset_id: "a_poster",
        trial_available: true,
        events: [
          { at_sec: 4, title: "检测到 1 人进店", result: "记录进店客流 +1", intent: "observe" },
        ],
      },
      {
        id: "hall-demo",
        camera_id: "hall",
        title: "店内效果",
        available: false,
        degrade_reason: "暂无预渲染演示媒体",
        input_asset_id: null,
        result_asset_id: null,
        poster_asset_id: null,
        trial_available: false,
        events: [],
      },
    ],
  },
  application: {
    mode: "create_cameras",
    camera_count: 1,
    rule_count: 1,
    auto_start: false,
    warnings: [],
  },
  privacy: { processing: "local", uploads_frames: false },
  readme_html: "<p>详细说明文档</p>",
  min_opencam_version: "0.2.0",
  format_version: 2,
};

function stubFetch(detailResp: { data: unknown; status?: number }, cameras: unknown[] = []) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/packs/")) {
        return jsonResp(detailResp.data, detailResp.status ?? 200);
      }
      if (url.startsWith("/api/cameras")) return jsonResp(cameras);
      return jsonResp({ detail: url }, 404);
    }),
  );
}

beforeEach(() => {
  nextNav.pathname = "/marketplace/fast-food";
  // jsdom 不实现媒体播放
  window.HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  window.HTMLMediaElement.prototype.pause = vi.fn();
});

describe("PackDetailPage", () => {
  it("渲染 Hero、业务结果、机位卡、限制与应用影响", async () => {
    stubFetch({ data: detail });
    render(<PackDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "快餐店" })).toBeInTheDocument();
    });
    expect(screen.getByText(/看清门口客流/)).toBeInTheDocument();
    expect(screen.getByText(/本机运行 · 数据不出本机/)).toBeInTheDocument();
    // 业务结果
    expect(screen.getByText("掌握进出店客流")).toBeInTheDocument();
    // 机位卡
    expect(screen.getByText("统计进出店客流的方向与数量")).toBeInTheDocument();
    expect(screen.getByText(/安装建议：正对或斜对门口/)).toBeInTheDocument();
    expect(screen.getByText(/门口越线计数/)).toBeInTheDocument();
    // 限制与应用影响
    expect(screen.getByText("仅识别人形目标，不能识别身份")).toBeInTheDocument();
    expect(screen.getByText(/新建 1 路停止态摄像头/)).toBeInTheDocument();
    // 说明文档
    expect(screen.getByText("详细说明文档")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "← 返回方案市场" })).toHaveAttribute(
      "href",
      "/marketplace",
    );
  });

  it("效果工作台：默认播放结果媒体，可切换原始画面、点击事件跳转", async () => {
    stubFetch({ data: detail });
    const { container } = render(<PackDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "门口进出客流" })).toBeInTheDocument();
    });
    const video = container.querySelector("video");
    expect(video).not.toBeNull();
    // 默认结果媒体
    expect(video?.getAttribute("src")).toBe("/api/packs/fast-food/assets/a_out");
    expect(video?.getAttribute("poster")).toBe("/api/packs/fast-food/assets/a_poster");

    // 切换原始画面
    fireEvent.click(screen.getByRole("button", { name: "原始画面" }));
    await waitFor(() => {
      expect(container.querySelector("video")?.getAttribute("src")).toBe(
        "/api/packs/fast-food/assets/a_in",
      );
    });

    // 点击事件跳转对应时刻
    fireEvent.click(screen.getByRole("button", { name: /检测到 1 人进店/ }));
    expect(window.HTMLMediaElement.prototype.play).toHaveBeenCalled();
  });

  it("无媒体场景降级为文字说明，不出现空白播放器", async () => {
    stubFetch({ data: detail });
    const { container } = render(<PackDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "店内效果" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("tab", { name: "店内效果" }));

    await waitFor(() => {
      expect(screen.getByText("暂无预渲染演示媒体")).toBeInTheDocument();
    });
    expect(container.querySelector("video")).toBeNull();
  });

  it("不兼容包展示原因并禁用应用，演示仍可查看", async () => {
    stubFetch({
      data: {
        ...detail,
        availability: "incompatible",
        unavailable_reason: "需要 open-cam >= 9.9.9，当前版本 0.1.0",
      },
    });
    render(<PackDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/需要 open-cam >= 9.9.9/)).toBeInTheDocument();
    });
    expect(screen.getByText("版本不兼容")).toBeInTheDocument();
    const applyButtons = screen.getAllByRole("button", { name: "应用方案" });
    for (const btn of applyButtons) {
      expect(btn).toBeDisabled();
    }
    // 演示媒体仍渲染
    expect(document.querySelector("video")).not.toBeNull();
  });

  it("旧包需明确选择摄像头后才可应用", async () => {
    stubFetch(
      {
        data: {
          ...detail,
          id: "restaurant",
          name: "餐饮店",
          application: {
            mode: "existing_camera",
            camera_count: 1,
            rule_count: 3,
            auto_start: false,
            warnings: ["需要选择一台已有摄像头后再应用"],
          },
        },
      },
      [{ id: 1, name: "门口" }],
    );
    render(<PackDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("选择要应用的摄像头")).toBeInTheDocument();
    });
    expect(screen.getByText(/该方案应用到一台已有摄像头/)).toBeInTheDocument();
    // 不默认第一台：未选择时应用按钮禁用
    const applyButtons = screen.getAllByRole("button", { name: "应用方案" });
    for (const btn of applyButtons) {
      expect(btn).toBeDisabled();
    }
  });

  it("404 时展示中文错误与返回入口", async () => {
    stubFetch({ data: { detail: "方案包不存在: no-such" }, status: 404 });
    render(<PackDetailPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText("方案包不存在或已卸载。")).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: "← 返回方案市场" })).toHaveAttribute(
      "href",
      "/marketplace",
    );
  });
});
