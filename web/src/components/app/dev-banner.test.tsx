import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { DevBanner } from "./dev-banner";

afterEach(() => {
  cleanup();
});

describe("DevBanner", () => {
  it("shows confirm on need_apply", () => {
    render(
      <DevBanner
        status={{
          reload_on: true,
          state: "need_apply",
          title: "待执行数据库迁移",
          detail: "确认后将重启进程（摄像头会中断几秒）",
          steps: [],
          can_apply: true,
        }}
        health="ok"
        onApply={() => {}}
      />,
    );
    expect(screen.getByRole("button", { name: "确认并重启" })).toBeEnabled();
  });

  it("disables confirm on need_revision", () => {
    render(
      <DevBanner
        status={{
          reload_on: true,
          state: "need_revision",
          title: "表结构已改，还没有迁移脚本",
          detail: "请先 make revision",
          steps: [],
          can_apply: false,
        }}
        health="ok"
        onApply={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: "确认并重启" })).toBeNull();
    expect(screen.getByText(/revision/)).toBeInTheDocument();
  });

  it("shows loading when health is down", () => {
    render(
      <DevBanner
        status={{
          reload_on: true,
          state: "idle",
          title: "",
          detail: "",
          steps: [],
          can_apply: false,
        }}
        health="down"
        onApply={() => {}}
      />,
    );
    expect(screen.getByText("正在加载…")).toBeInTheDocument();
  });

  it("shows applying while confirmed restart is in flight", () => {
    render(
      <DevBanner
        status={{
          reload_on: true,
          state: "need_apply",
          title: "待执行数据库迁移",
          detail: "",
          steps: [],
          can_apply: true,
        }}
        health="down"
        applying
        onApply={() => {}}
      />,
    );
    expect(screen.getByText("正在执行数据库迁移…")).toBeInTheDocument();
    expect(screen.queryByText("待执行数据库迁移")).toBeNull();
  });

  it("renders nothing when idle and healthy", () => {
    const { container } = render(
      <DevBanner
        status={{
          reload_on: true,
          state: "idle",
          title: "热加载已开启",
          detail: "",
          steps: [],
          can_apply: false,
        }}
        health="ok"
        onApply={() => {}}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
