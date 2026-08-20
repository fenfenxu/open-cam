import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextNav } from "@/test/next-nav";
import { ModelsPage } from "./models";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

beforeEach(() => {
  nextNav.pathname = "/models";
  nextNav.search = "";
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

const builtinAsset = {
  id: 1,
  name: "YOLOv8 Nano（系统内置）",
  description: "系统默认目标检测模型",
  origin_type: "builtin",
  distribution_type: "private",
  model_kind: "object_detection",
  capabilities: ["person_detection"],
  input_contract: {},
  output_contract: {},
  task_key: "person_detection",
  solution_pack_id: null,
  training_task_id: null,
  metadata: {},
  status: "active",
  created_at: 1,
  updated_at: 1,
};

describe("ModelsPage", () => {
  it("renders origin, distribution and kind badges for each asset", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/models/assets")) return jsonResp([builtinAsset]);
        return jsonResp({ detail: url }, 404);
      }),
    );
    render(<ModelsPage />, { wrapper });
    await screen.findByText("YOLOv8 Nano（系统内置）");
    expect(screen.getByText("系统内置")).toBeTruthy();
    expect(screen.getByText("仅本机使用")).toBeTruthy();
    expect(screen.getByText("目标检测")).toBeTruthy();
    expect(screen.getAllByText(/person_detection/).length).toBeGreaterThan(0);
  });

  it("registers an asset with origin, distribution and capabilities", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/models/assets" && init?.method === "POST") {
        return jsonResp({ ...builtinAsset, id: 2, name: "工服识别模型" }, 201);
      }
      if (url.startsWith("/api/models/assets")) return jsonResp([]);
      return jsonResp({ detail: url }, 404);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ModelsPage />, { wrapper });
    await screen.findByText("还没有模型资产。");

    fireEvent.change(screen.getByPlaceholderText("例如：门店人员检测模型"), {
      target: { value: "工服识别模型" },
    });
    fireEvent.change(screen.getByLabelText("能力标签（逗号分隔，可选）"), {
      target: { value: "uniform_classification, uniform.attribute" },
    });
    fireEvent.click(screen.getByRole("button", { name: /登记模型/ }));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(
        ([input, init]) => String(input) === "/api/models/assets" && init?.method === "POST",
      );
      expect(call).toBeTruthy();
      const body = JSON.parse(String(call![1]?.body));
      expect(body.name).toBe("工服识别模型");
      expect(body.origin_type).toBe("uploaded");
      expect(body.distribution_type).toBe("private");
      expect(body.capabilities).toEqual(["uniform_classification", "uniform.attribute"]);
    });
  });

  it("loads an asset into the form when editing", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url.startsWith("/api/models/assets")) return jsonResp([builtinAsset]);
        return jsonResp({ detail: url }, 404);
      }),
    );
    render(<ModelsPage />, { wrapper });
    await screen.findByText("YOLOv8 Nano（系统内置）");
    fireEvent.click(screen.getByRole("button", { name: /编辑/ }));
    await screen.findByText("编辑模型资产");
    expect(
      (screen.getByPlaceholderText("例如：门店人员检测模型") as HTMLInputElement).value,
    ).toBe("YOLOv8 Nano（系统内置）");
    expect((screen.getByLabelText("能力标签（逗号分隔，可选）") as HTMLInputElement).value).toBe(
      "person_detection",
    );
  });
});
