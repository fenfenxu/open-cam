import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { PageHeader } from "@/components/app/page-header";
import { VideoWall } from "@/components/app/video-wall";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import { SOURCE_TYPE_NAMES, type Camera, type ModelVersion } from "@/lib/cameras";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

export function CameraDetailPage() {
  const { id } = useParams();
  const queryClient = useQueryClient();
  const cameraId = Number(id);

  const cameraQuery = useQuery({
    queryKey: ["camera", cameraId],
    queryFn: () => api<Camera>(`/cameras/${cameraId}`),
    enabled: Number.isFinite(cameraId),
  });

  const modelsQuery = useQuery({
    queryKey: ["models"],
    queryFn: () => api<ModelVersion[]>("/models"),
    enabled: Number.isFinite(cameraId),
  });

  useEffect(() => {
    if (cameraQuery.isError) toast.error(errorMessage(cameraQuery.error));
  }, [cameraQuery.isError, cameraQuery.error]);

  const toggleCamera = useMutation({
    mutationFn: (act: "start" | "stop") =>
      api(`/cameras/${cameraId}/${act}`, { method: "POST" }),
    onSuccess: async (_data, act) => {
      toast.success(act === "start" ? "已启动" : "已停止");
      await queryClient.invalidateQueries({ queryKey: ["camera", cameraId] });
      await queryClient.invalidateQueries({ queryKey: ["cameras"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const rollback = useMutation({
    mutationFn: (modelId: number) =>
      api<{ reason?: string }>(`/models/${modelId}/rollback`, { method: "POST" }),
    onSuccess: async (data) => {
      toast.success(data.reason || "已回滚");
      await queryClient.invalidateQueries({ queryKey: ["models"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  if (!Number.isFinite(cameraId)) {
    return <p className="text-sm text-muted-foreground">缺少摄像头 id</p>;
  }

  const cam = cameraQuery.data;
  const liveModels = (modelsQuery.data ?? []).filter((m) => m.status === "live");

  return (
    <div className="space-y-4">
      <p className="text-sm">
        <Link className="underline" to="/cameras">
          ← 摄像头列表
        </Link>
      </p>
      <PageHeader
        title={cam?.name ?? "摄像头详情"}
        description={
          cam
            ? `${SOURCE_TYPE_NAMES[cam.source_type] || cam.source_type} · ${cam.source_uri}`
            : undefined
        }
        actions={
          cam ? (
            <div className="flex items-center gap-2">
              <Badge variant="secondary">{cam.status}</Badge>
              <Button variant="outline" render={<Link to={`/rules?camera=${cam.id}`} />}>
                配置规则
              </Button>
              <Button
                variant="outline"
                onClick={() =>
                  toggleCamera.mutate(cam.status === "running" ? "stop" : "start")
                }
              >
                {cam.status === "running" ? "停止" : "启动"}
              </Button>
            </div>
          ) : null
        }
      />

      {cameraQuery.isPending ? (
        <p className="text-sm text-muted-foreground">正在加载摄像头…</p>
      ) : null}
      {cameraQuery.isError ? (
        <p className="text-sm text-destructive">{errorMessage(cameraQuery.error)}</p>
      ) : null}
      {cam ? <VideoWall cameras={[cam]} showReplay /> : null}

      <section className="space-y-2">
        <h2 className="text-lg font-medium">已部署模型</h2>
        {modelsQuery.isError ? (
          <p className="text-sm text-muted-foreground">
            模型列表失败：{errorMessage(modelsQuery.error)}
          </p>
        ) : liveModels.length === 0 ? (
          <p className="text-sm text-muted-foreground">暂无线上模型。到「模型训练」完成部署。</p>
        ) : (
          liveModels.map((m) => (
            <div key={m.id} className="rounded-lg border p-3">
              <div className="flex items-center gap-2">
                {m.slot_key}
                <Badge variant="outline">{m.status}</Badge>
              </div>
              <p className="font-mono text-xs text-muted-foreground">
                任务 {m.task_id} · 版本 {m.id}
              </p>
              <Button
                className="mt-2"
                size="sm"
                variant="outline"
                onClick={() => rollback.mutate(m.id)}
              >
                回滚
              </Button>
            </div>
          ))
        )}
      </section>
    </div>
  );
}
