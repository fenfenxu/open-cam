import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import Link from "next/link";
import { FootfallChart } from "@/components/app/footfall-chart";
import { PageHeader } from "@/components/app/page-header";
import { Badge } from "@/components/ui/badge";
import { api, fmtTime, resolveApiUrl } from "@/lib/api";
import { SOURCE_TYPE_NAMES, type Camera } from "@/lib/cameras";
import {
  FOOTFALL_IN_COLOR,
  FOOTFALL_OUT_COLOR,
  type FootfallStats,
} from "@/lib/footfall";

type EventSummary = { ts: number };

function SnapshotThumb({ camera }: { camera: Camera }) {
  const running = camera.status === "running";
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setTick(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [running, camera.id]);

  const src = running
    ? resolveApiUrl(`/cameras/${camera.id}/snapshot.jpg?t=${tick || Date.now()}`)
    : resolveApiUrl(`/cameras/${camera.id}/snapshot.jpg`);

  return (
    <img
      className="aspect-video w-full rounded-md bg-black object-contain"
      alt={running ? `${camera.name} 画面` : "暂无画面"}
      src={src}
      onError={(ev) => {
        ev.currentTarget.style.visibility = "hidden";
      }}
    />
  );
}

function CameraCard({ camera }: { camera: Camera }) {
  const eventsQuery = useQuery({
    queryKey: ["events", "dashboard", camera.id],
    queryFn: () => api<EventSummary[]>(`/events?camera_id=${camera.id}&limit=50`),
    refetchInterval: 5000,
  });
  const footfallQuery = useQuery({
    queryKey: ["stats", "footfall", camera.id],
    queryFn: () => api<FootfallStats>(`/api/stats/footfall?camera_id=${camera.id}`),
    refetchInterval: 30000,
  });

  const events = eventsQuery.data ?? [];
  const foot = footfallQuery.data;
  const total = (foot?.total_in ?? 0) + (foot?.total_out ?? 0);

  return (
    <Link
      href={`/cameras/${camera.id}`}
      className="block space-y-3 rounded-lg border p-3 transition-colors hover:bg-muted/40"
    >
      <header className="flex items-center justify-between gap-2">
        <h3 className="text-sm font-medium">{camera.name}</h3>
        <Badge variant={camera.status === "running" ? "default" : "secondary"}>
          {camera.status}
        </Badge>
      </header>
      <p className="font-mono text-xs text-muted-foreground">
        {SOURCE_TYPE_NAMES[camera.source_type] || camera.source_type} · {camera.source_uri}
      </p>
      <SnapshotThumb camera={camera} />
      <p className="text-xs text-muted-foreground">
        最近事件：{eventsQuery.isSuccess ? events.length : "—"} · 最新：
        {events.length ? fmtTime(events[0].ts) : eventsQuery.isSuccess ? "无" : "—"}
      </p>
      <div className="space-y-1 text-xs text-muted-foreground">
        <p>
          今日客流
          {total ? ` 进 ${foot?.total_in} / 出 ${foot?.total_out}` : ""}{" "}
          <span style={{ color: FOOTFALL_IN_COLOR }}>■ 进</span>{" "}
          <span style={{ color: FOOTFALL_OUT_COLOR }}>■ 出</span>
        </p>
        {total > 0 && foot ? (
          <FootfallChart buckets={foot.buckets} />
        ) : (
          <p>暂无客流数据（先配置「越线计数」规则）</p>
        )}
      </div>
    </Link>
  );
}

export function DashboardPage() {
  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/cameras"),
  });

  const cameras = camerasQuery.data ?? [];

  return (
    <div className="space-y-4">
      <PageHeader title="仪表盘" description="各路画面与今日客流" />
      {camerasQuery.isPending && <p className="text-sm text-muted-foreground">正在加载…</p>}
      {camerasQuery.isError && (
        <p className="text-sm text-destructive">加载摄像头失败</p>
      )}
      {camerasQuery.isSuccess && cameras.length === 0 && (
        <p className="text-sm text-muted-foreground">
          还没有摄像头，去
          <Link className="underline" href="/cameras">
            「摄像头」
          </Link>
          页添加一路。
        </p>
      )}
      {cameras.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {cameras.map((camera) => (
            <CameraCard key={camera.id} camera={camera} />
          ))}
        </div>
      )}
    </div>
  );
}
