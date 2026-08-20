import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DetailDrawer } from "./detail-drawer";

describe("DetailDrawer", () => {
  it("支持用键盘和按钮调整宽度", async () => {
    render(
      <DetailDrawer open onOpenChange={() => undefined} title="事件详情">
        <div>内容</div>
      </DetailDrawer>,
    );

    const separator = screen.getByRole("separator", { name: "调整详情抽屉宽度" });
    const content = document.querySelector<HTMLElement>('[data-slot="sheet-content"]');
    expect(content).not.toBeNull();
    expect(content).not.toHaveClass("relative");
    expect(content?.style.top).toBe("0px");
    expect(content?.style.right).toBe("0px");
    expect(content?.style.bottom).toBe("0px");
    expect(separator).toHaveAttribute("aria-valuenow", "720");

    fireEvent.keyDown(separator, { key: "ArrowLeft" });
    expect(separator).toHaveAttribute("aria-valuenow", "760");

    fireEvent.click(screen.getByRole("button", { name: "展开详情抽屉" }));
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "收回详情抽屉" })).toBeInTheDocument();
    });
    expect(separator).toHaveAttribute("aria-valuenow", String(window.innerWidth - 24));

    fireEvent.click(screen.getByRole("button", { name: "收回详情抽屉" }));
    expect(separator).toHaveAttribute("aria-valuenow", "720");
  });
});
