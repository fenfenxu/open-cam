export type FootfallBucket = {
  hour: number;
  in: number;
  out: number;
};

export type FootfallStats = {
  camera_id: number;
  date: string;
  buckets: FootfallBucket[];
  total_in: number;
  total_out: number;
};

export const FOOTFALL_IN_COLOR = "#3b9eff";
export const FOOTFALL_OUT_COLOR = "#e5b545";
export const FOOTFALL_TICK_COLOR = "#8a94a3";

type FootfallCanvas = {
  width: number;
  height: number;
  getContext(contextId: string): CanvasRenderingContext2D | null;
};

/** 24 小时进/出双列柱，与现网 dashboard.js 同一套像素约定。 */
export function drawFootfall(
  canvas: FootfallCanvas,
  buckets: FootfallBucket[],
  tickColor = FOOTFALL_TICK_COLOR,
): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const max = Math.max(1, ...buckets.map((b) => Math.max(b.in, b.out)));
  const groupW = W / 24;
  const barW = Math.max(1, (groupW - 3) / 2);
  buckets.forEach((b, i) => {
    const x0 = i * groupW + 1;
    const inH = (b.in / max) * (H - 12);
    const outH = (b.out / max) * (H - 12);
    ctx.fillStyle = FOOTFALL_IN_COLOR;
    ctx.fillRect(x0, H - inH, barW, inH);
    ctx.fillStyle = FOOTFALL_OUT_COLOR;
    ctx.fillRect(x0 + barW + 1, H - outH, barW, outH);
  });
  ctx.fillStyle = tickColor;
  ctx.font = "9px sans-serif";
  ctx.fillText("0", 1, H - 1);
  ctx.fillText("12", 12 * groupW, H - 1);
  ctx.fillText("23", 23 * groupW, H - 1);
}
