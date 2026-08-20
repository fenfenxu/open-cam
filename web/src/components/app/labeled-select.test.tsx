import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LabeledSelect } from "./labeled-select";

afterEach(cleanup);

describe("LabeledSelect", () => {
  it("切换选中值后仍保留字段名", () => {
    const items = [
      { value: "__all__", label: "全部类型" },
      { value: "loitering", label: "徘徊逗留" },
    ];
    const { rerender } = render(
      <LabeledSelect
        label="类型"
        value="__all__"
        onChange={() => undefined}
        items={items}
      />,
    );

    expect(screen.getByRole("combobox", { name: "类型：全部类型" })).toBeInTheDocument();

    rerender(
      <LabeledSelect
        label="类型"
        value="loitering"
        onChange={() => undefined}
        items={items}
      />,
    );

    expect(screen.getByRole("combobox", { name: "类型：徘徊逗留" })).toBeInTheDocument();
  });
});
