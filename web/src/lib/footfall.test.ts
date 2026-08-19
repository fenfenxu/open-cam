import { describe, expect, it, vi } from "vitest";
import {
  FOOTFALL_IN_COLOR,
  FOOTFALL_OUT_COLOR,
  drawFootfall,
  type FootfallBucket,
} from "./footfall";

function emptyBuckets(): FootfallBucket[] {
  return Array.from({ length: 24 }, (_, hour) => ({ hour, in: 0, out: 0 }));
}

describe("drawFootfall", () => {
  it("draws in/out bars and hour ticks", () => {
    const barStyles: string[] = [];
    const fillText = vi.fn();
    const ctx = {
      clearRect: vi.fn(),
      fillRect: vi.fn(() => {
        barStyles.push(ctx.fillStyle);
      }),
      fillText,
      fillStyle: "",
      font: "",
    };
    const canvas = {
      width: 240,
      height: 64,
      getContext: () => ctx as unknown as CanvasRenderingContext2D,
    };
    const buckets = emptyBuckets();
    buckets[0] = { hour: 0, in: 4, out: 2 };

    drawFootfall(canvas, buckets);

    expect(ctx.clearRect).toHaveBeenCalledWith(0, 0, 240, 64);
    expect(barStyles.filter((s) => s === FOOTFALL_IN_COLOR).length).toBe(24);
    expect(barStyles.filter((s) => s === FOOTFALL_OUT_COLOR).length).toBe(24);
    expect(fillText.mock.calls.map((c) => c[0])).toEqual(["0", "12", "23"]);
  });
});
