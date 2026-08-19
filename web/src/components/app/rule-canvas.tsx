import { useEffect, useRef } from "react";
import type { CameraRule, Point } from "@/lib/rules";

const EXISTING_POLY = "#3b9eff";
const EXISTING_LINE = "#34c77b";
const DRAFT = "#e5b545";

function drawShape(
  ctx: CanvasRenderingContext2D,
  pts: Point[],
  color: string,
  scale: number,
  closed: boolean,
  isLine: boolean,
) {
  if (pts.length === 0) return;
  ctx.strokeStyle = color;
  ctx.fillStyle = `${color}33`;
  ctx.lineWidth = 2;
  ctx.beginPath();
  ctx.moveTo(pts[0][0] * scale, pts[0][1] * scale);
  for (const [x, y] of pts.slice(1)) ctx.lineTo(x * scale, y * scale);
  if (closed && !isLine) {
    ctx.closePath();
    ctx.fill();
  }
  ctx.stroke();
  for (const [x, y] of pts) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(x * scale, y * scale, 3, 0, Math.PI * 2);
    ctx.fill();
  }
}

export function RuleCanvas({
  snapshotUrl,
  existing,
  points,
  zoneShape,
  onPointsChange,
  onReady,
  onHint,
}: {
  snapshotUrl: string;
  existing: CameraRule[];
  points: Point[];
  zoneShape: "polygon" | "line";
  onPointsChange: (next: Point[]) => void;
  onReady: (ok: boolean) => void;
  onHint?: (message: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const scaleRef = useRef(1);
  const pointsRef = useRef(points);
  const existingRef = useRef(existing);
  const zoneRef = useRef(zoneShape);
  const onReadyRef = useRef(onReady);
  const onPointsRef = useRef(onPointsChange);
  const onHintRef = useRef(onHint);

  pointsRef.current = points;
  existingRef.current = existing;
  zoneRef.current = zoneShape;
  onReadyRef.current = onReady;
  onPointsRef.current = onPointsChange;
  onHintRef.current = onHint;

  function redraw() {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !img || !ctx) return;
    const scale = scaleRef.current;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
    for (const rule of existingRef.current) {
      const poly = rule.params?.polygon as Point[] | undefined;
      const line = rule.params?.line as Point[] | undefined;
      if (poly) drawShape(ctx, poly, EXISTING_POLY, scale, true, false);
      if (line) drawShape(ctx, line, EXISTING_LINE, scale, false, true);
    }
    const isLine = zoneRef.current === "line";
    drawShape(ctx, pointsRef.current, DRAFT, scale, !isLine, isLine);
  }

  useEffect(() => {
    redraw();
  }, [points, existing, zoneShape, snapshotUrl]);

  useEffect(() => {
    const img = new Image();
    imgRef.current = null;
    img.onload = () => {
      const canvas = canvasRef.current;
      const wrap = wrapRef.current;
      if (!canvas) return;
      const maxW = Math.min(900, wrap?.clientWidth || 900);
      const scale = Math.min(1, maxW / img.naturalWidth);
      scaleRef.current = scale;
      canvas.width = Math.round(img.naturalWidth * scale);
      canvas.height = Math.round(img.naturalHeight * scale);
      imgRef.current = img;
      onPointsRef.current([]);
      onReadyRef.current(true);
      redraw();
    };
    img.onerror = () => {
      imgRef.current = null;
      onReadyRef.current(false);
    };
    img.src = snapshotUrl;
    return () => {
      img.onload = null;
      img.onerror = null;
    };
    // snapshotUrl 变了才重新拉底图；points 变化走上面的 redraw
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snapshotUrl]);

  return (
    <div ref={wrapRef} className="overflow-auto">
      <canvas
        ref={canvasRef}
        className="max-w-full cursor-crosshair rounded-md border"
        onClick={(ev) => {
          const canvas = canvasRef.current;
          if (!canvas || !imgRef.current) return;
          let next = pointsRef.current;
          if (zoneRef.current === "line" && next.length >= 2) next = [];
          const rect = canvas.getBoundingClientRect();
          const scale = scaleRef.current;
          onPointsChange([
            ...next,
            [
              Math.round((ev.clientX - rect.left) / scale),
              Math.round((ev.clientY - rect.top) / scale),
            ],
          ]);
        }}
        onDoubleClick={() => {
          const n = pointsRef.current.length;
          const min = zoneRef.current === "line" ? 2 : 3;
          if (n >= min) onHintRef.current?.(zoneRef.current === "line" ? "线段已完成" : `多边形已闭合（${n} 个顶点）`);
        }}
      />
    </div>
  );
}
