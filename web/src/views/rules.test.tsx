import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextNav } from "@/test/next-nav";
import { RulesPage } from "./rules";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

beforeEach(() => {
  nextNav.pathname = "/rules";
  nextNav.search = "camera=1";
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

const presets = {
  presets: [
    {
      type: "zone_intrusion",
      display_name: "区域入侵",
      tagline: "进入区域告警",
      description: "画多边形",
      scenarios: ["顾客进入后厨告警"],
      needs_zone: true,
      zone_shape: "polygon",
      fields: [
        { key: "name", label: "规则名称", kind: "text", default: "区域入侵" },
        { key: "classes", label: "目标类别", kind: "class", default: ["person"] },
        { key: "cooldown", label: "冷却时间", kind: "number", default: 30, unit: "秒" },
      ],
    },
    {
      type: "object_count",
      display_name: "人数统计",
      tagline: "整画面超员",
      description: "不需要画区域",
      scenarios: ["店内超员"],
      needs_zone: false,
      zone_shape: null,
      fields: [
        { key: "name", label: "规则名称", kind: "text", default: "人数统计" },
        { key: "threshold", label: "数量超过", kind: "number", default: 10, unit: "个" },
        { key: "classes", label: "目标类别", kind: "class", default: ["person"] },
        { key: "cooldown", label: "冷却时间", kind: "number", default: 300, unit: "秒" },
      ],
    },
  ],
  common_classes: [{ id: "person", name: "人" }],
  classes_note: "常用类别",
};

describe("RulesPage", () => {
  it("asks to add a camera when the list is empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/cameras") return jsonResp([]);
        if (url === "/api/rules/presets") return jsonResp(presets);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<RulesPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByText(/请先添加摄像头/)).toBeInTheDocument();
    });
  });

  it("lists five-type Chinese names and existing rules", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/cameras") {
          return jsonResp([{ id: 1, name: "门口", source_type: "file", source_uri: "/a.mp4", status: "running" }]);
        }
        if (url === "/api/rules/presets") return jsonResp(presets);
        if (url === "/cameras/1/rules") {
          return jsonResp([
            {
              id: 9,
              name: "后厨",
              type: "zone_intrusion",
              cooldown: 30,
              enabled: true,
              params: { polygon: [[0, 0], [1, 0], [1, 1]], classes: ["person"] },
            },
          ]);
        }
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<RulesPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "区域入侵" })).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "人数统计" })).toBeInTheDocument();
      expect(screen.getByText("后厨")).toBeInTheDocument();
    });
    expect(screen.getByText(/3 边形区域/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除" })).toBeInTheDocument();
  });

  it("opens the parameter step with 区域入侵 copy", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/cameras") {
          return jsonResp([{ id: 1, name: "门口", source_type: "file", source_uri: "/a.mp4", status: "stopped" }]);
        }
        if (url === "/api/rules/presets") return jsonResp(presets);
        if (url === "/cameras/1/rules") return jsonResp([]);
        return jsonResp({ detail: url }, 404);
      }),
    );

    render(<RulesPage />, { wrapper });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "区域入侵" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("heading", { name: "区域入侵" }));
    expect(screen.getByText("规则名称")).toBeInTheDocument();
    expect(screen.getByText("意图")).toBeInTheDocument();
    expect(screen.getByText("升格方式")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一步" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("该摄像头还没有规则，从上面的场景卡片开始吧。")).toBeInTheDocument();
    });
  });
});
