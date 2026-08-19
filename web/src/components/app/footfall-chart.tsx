import { useEffect, useRef } from "react";
import { drawFootfall, type FootfallBucket } from "@/lib/footfall";
import { cn } from "@/lib/utils";

export function FootfallChart({
  buckets,
  className,
}: {
  buckets: FootfallBucket[];
  className?: string;
}) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    drawFootfall(canvas, buckets);
  }, [buckets]);

  return (
    <canvas
      ref={ref}
      width={260}
      height={64}
      className={cn("h-16 w-full", className)}
      aria-label="24 小时进/出客流"
    />
  );
}
