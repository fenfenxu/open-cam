import { useEffect, useRef, useState } from "react";
import { resolveApiUrl } from "@/lib/api";
import { CAMERA_STATUS_NAMES } from "@/lib/labels";
import { cn } from "@/lib/utils";
import type { Camera } from "@/lib/cameras";

const RTSP_REPLAY_COPY = "该源为直播流，不支持回放。";

function LivePane({ camera }: { camera: Camera }) {
  const imgRef = useRef<HTMLImageElement>(null);
  const running = camera.status === "running";

  useEffect(() => {
    const img = imgRef.current;
    return () => {
      if (img) img.src = "";
    };
  }, [camera.id, running]);

  if (running) {
    return (
      <img
        ref={imgRef}
        className="w-full rounded-md bg-black"
        alt={`${camera.name} 直播`}
        src={resolveApiUrl(`/api/cameras/${camera.id}/live.mjpg`)}
      />
    );
  }

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">摄像头未运行。</p>
      <img
        className="max-h-64 w-full rounded-md border object-contain"
        alt="暂无画面"
        src={resolveApiUrl(`/api/cameras/${camera.id}/snapshot.jpg`)}
        onError={(ev) => {
          ev.currentTarget.style.display = "none";
        }}
      />
    </div>
  );
}

function ReplayPane({ camera }: { camera: Camera }) {
  const [failed, setFailed] = useState(false);

  if (camera.source_type !== "file") {
    return <p className="text-sm text-muted-foreground">{RTSP_REPLAY_COPY}</p>;
  }
  if (failed) {
    return <p className="text-sm text-muted-foreground">浏览器无法播放该格式。</p>;
  }
  return (
    <video
      className="w-full rounded-md bg-black"
      controls
      playsInline
      src={resolveApiUrl(`/api/cameras/${camera.id}/source`)}
      onError={() => setFailed(true)}
    />
  );
}

export function VideoWall({
  cameras,
  showReplay = false,
  className,
}: {
  cameras: Camera[];
  showReplay?: boolean;
  className?: string;
}) {
  if (cameras.length === 0) return null;

  return (
    <div
      className={cn(
        "grid gap-4",
        cameras.length === 1 ? "grid-cols-1" : "grid-cols-1 sm:grid-cols-2",
        className,
      )}
    >
      {cameras.map((camera) => (
        <article key={camera.id} className="space-y-3 rounded-lg border p-3">
          <header className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">{camera.name}</h3>
            <span className="font-mono text-xs text-muted-foreground">
              {CAMERA_STATUS_NAMES[camera.status] || camera.status}
            </span>
          </header>
          <section className="space-y-2">
            <h4 className="text-xs font-medium text-muted-foreground">直播</h4>
            <LivePane camera={camera} />
          </section>
          {showReplay ? (
            <section className="space-y-2">
              <h4 className="text-xs font-medium text-muted-foreground">回放</h4>
              <ReplayPane camera={camera} />
            </section>
          ) : null}
        </article>
      ))}
    </div>
  );
}

export { RTSP_REPLAY_COPY };
