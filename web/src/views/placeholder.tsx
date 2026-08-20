import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "@/lib/api";

type Camera = {
  id: number;
  name: string;
  status: string;
};

export function PlaceholderPage({ title }: { title: string }) {
  const cameras = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-medium">{title}</h1>
      <p className="text-sm text-muted-foreground">页面尚未迁移，先用 GET /api/cameras 验证接口。</p>
      {cameras.isPending && <p className="text-sm">正在加载摄像头…</p>}
      {cameras.isError && (
        <p className="text-sm text-destructive">
          {cameras.error instanceof ApiError ? cameras.error.message : "加载失败"}
        </p>
      )}
      {cameras.data && (
        <ul className="list-disc space-y-1 pl-5 text-sm">
          {cameras.data.length === 0 && <li>暂无摄像头</li>}
          {cameras.data.map((cam) => (
            <li key={cam.id}>
              {cam.name}（#{cam.id} · {cam.status}）
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
