import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DataTable } from "./data-table";

describe("DataTable", () => {
  it("renders empty state copy", () => {
    render(
      <DataTable
        columns={[{ accessorKey: "name", header: "名称" } as const]}
        data={[]}
      />,
    );
    expect(screen.getByText("暂无事件")).toBeInTheDocument();
  });
});
