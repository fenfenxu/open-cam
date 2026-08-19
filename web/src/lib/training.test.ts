import { describe, expect, it } from "vitest";
import { inferStep, type TrainingTask } from "./training";

describe("inferStep", () => {
  it("starts at 说需求 when there is no task", () => {
    expect(inferStep(null)).toBe(1);
  });

  it("jumps to 评估 after train.done", () => {
    const task: TrainingTask = {
      task_id: "t1",
      train: { status: "done", result: { conclusion: "ok" } },
    };
    expect(inferStep(task)).toBe(6);
  });

  it("stays on 训练 while running", () => {
    const task: TrainingTask = { task_id: "t1", train: { status: "running" } };
    expect(inferStep(task)).toBe(5);
  });
});
