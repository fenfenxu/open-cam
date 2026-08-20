import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell } from "./app-shell";

afterEach(() => {
  window.localStorage.removeItem("opencam-sidebar-collapsed");
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("AppShell", () => {
  it("可以收起和展开侧边菜单", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/health") return { ok: true };
        if (url === "/api/system/dev") {
          return {
            ok: true,
            json: async () => ({
              reload_on: true,
              state: "idle",
              title: "",
              detail: "",
              steps: [],
              can_apply: false,
            }),
          };
        }
        return { ok: false, status: 404, json: async () => ({}) };
      }),
    );

    render(
      <AppShell>
        <div>内容</div>
      </AppShell>,
      { wrapper },
    );

    const collapseButton = await screen.findByRole("button", { name: "收起菜单" });
    const aside = collapseButton.closest("aside");
    expect(aside).toHaveClass("w-56");

    fireEvent.click(collapseButton);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "展开菜单" })).toBeInTheDocument();
    });
    expect(aside).toHaveClass("w-16");

    fireEvent.click(screen.getByRole("button", { name: "展开菜单" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "收起菜单" })).toBeInTheDocument();
    });
    expect(aside).toHaveClass("w-56");
  });
});
