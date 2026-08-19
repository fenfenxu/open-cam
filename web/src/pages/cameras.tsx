import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import { Link } from "react-router";
import { toast } from "sonner";
import { z } from "zod";
import { DataTable, type DataTableColumn } from "@/components/app/data-table";
import { PageHeader } from "@/components/app/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import {
  SOURCE_TYPE_NAMES,
  dash,
  jsonBody,
  type Camera,
  type VideoAsset,
} from "@/lib/cameras";

const createSchema = z.object({
  name: z.string().trim().min(1, "请填写名称"),
  source_type: z.enum(["file", "rtsp"]),
  source_uri: z.string().trim().min(1, "请填写源地址"),
  autostart: z.boolean(),
});

type CreateForm = z.infer<typeof createSchema>;

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

export function CamerasPage() {
  const queryClient = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [names, setNames] = useState<Record<number, string>>({});
  const fileInputRef = useRef<HTMLInputElement>(null);

  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/cameras"),
  });
  const videosQuery = useQuery({
    queryKey: ["videos"],
    queryFn: () => api<VideoAsset[]>("/videos"),
  });

  useEffect(() => {
    if (camerasQuery.isError) toast.error(errorMessage(camerasQuery.error));
  }, [camerasQuery.isError, camerasQuery.error]);

  useEffect(() => {
    const next: Record<number, string> = {};
    for (const cam of camerasQuery.data ?? []) next[cam.id] = cam.name;
    setNames(next);
  }, [camerasQuery.data]);

  const form = useForm<CreateForm>({
    resolver: zodResolver(createSchema),
    defaultValues: {
      name: "",
      source_type: "file",
      source_uri: "",
      autostart: false,
    },
  });
  const sourceType = form.watch("source_type");

  const invalidateCameras = () => queryClient.invalidateQueries({ queryKey: ["cameras"] });

  const createCamera = useMutation({
    mutationFn: (body: CreateForm) => api<Camera>("/cameras", jsonBody("POST", body)),
    onSuccess: async () => {
      toast.success("摄像头已添加");
      setCreateOpen(false);
      form.reset();
      await invalidateCameras();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const renameCamera = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      api(`/cameras/${id}`, jsonBody("PUT", { name })),
    onSuccess: async () => {
      toast.success("已保存");
      await invalidateCameras();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const toggleCamera = useMutation({
    mutationFn: ({ id, act }: { id: number; act: "start" | "stop" }) =>
      api(`/cameras/${id}/${act}`, { method: "POST" }),
    onSuccess: async (_data, vars) => {
      toast.success(vars.act === "start" ? "已启动" : "已停止");
      await invalidateCameras();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const deleteCamera = useMutation({
    mutationFn: (id: number) => api(`/cameras/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      toast.success("已删除");
      await invalidateCameras();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const deleteVideo = useMutation({
    mutationFn: (id: number) => api(`/videos/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      toast.success("视频已删除");
      await queryClient.invalidateQueries({ queryKey: ["videos"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  async function uploadFile(file: File) {
    const body = new FormData();
    body.append("file", file);
    try {
      const resp = await fetch("/cameras/upload", { method: "POST", body });
      const payload = (await resp.json()) as { path?: string; detail?: unknown };
      if (!resp.ok) {
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : `HTTP ${resp.status}`,
        );
      }
      if (payload.path) form.setValue("source_uri", payload.path, { shouldValidate: true });
      toast.success("文件已上传");
      await queryClient.invalidateQueries({ queryKey: ["videos"] });
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  const cameraColumns = useMemo<DataTableColumn<Camera>[]>(
    () => [
      {
        accessorKey: "id",
        header: "ID",
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.id}</span>,
      },
      {
        accessorKey: "name",
        header: "名称",
        cell: ({ row }) => (
          <Input
            className="max-w-48"
            value={names[row.original.id] ?? row.original.name}
            onChange={(e) =>
              setNames((prev) => ({ ...prev, [row.original.id]: e.target.value }))
            }
            aria-label={`摄像头 ${row.original.id} 名称`}
          />
        ),
      },
      {
        accessorKey: "source_type",
        header: "类型",
        cell: ({ row }) => SOURCE_TYPE_NAMES[row.original.source_type] || row.original.source_type,
      },
      {
        accessorKey: "source_uri",
        header: "源地址",
        cell: ({ row }) => (
          <span className="font-mono text-xs break-all">{row.original.source_uri}</span>
        ),
      },
      {
        accessorKey: "status",
        header: "状态",
        cell: ({ row }) => <Badge variant="secondary">{row.original.status}</Badge>,
      },
      {
        id: "actions",
        header: "操作",
        cell: ({ row }) => {
          const cam = row.original;
          const running = cam.status === "running";
          return (
            <div className="flex flex-wrap gap-1">
              <Button size="xs" variant="outline" render={<Link to={`/cameras/${cam.id}`} />}>
                查看
              </Button>
              <Button size="xs" variant="outline" render={<Link to={`/rules?camera=${cam.id}`} />}>
                规则
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() => toggleCamera.mutate({ id: cam.id, act: running ? "stop" : "start" })}
              >
                {running ? "停止" : "启动"}
              </Button>
              <Button
                size="xs"
                variant="outline"
                onClick={() => {
                  const name = (names[cam.id] ?? cam.name).trim();
                  if (!name) {
                    toast.error("请填写名称");
                    return;
                  }
                  renameCamera.mutate({ id: cam.id, name });
                }}
              >
                保存
              </Button>
              <Button
                size="xs"
                variant="destructive"
                onClick={() => {
                  if (!window.confirm(`删除摄像头「${cam.name}」？`)) return;
                  deleteCamera.mutate(cam.id);
                }}
              >
                删除
              </Button>
            </div>
          );
        },
      },
    ],
    [names, toggleCamera, renameCamera, deleteCamera],
  );

  const videoColumns = useMemo<DataTableColumn<VideoAsset>[]>(
    () => [
      {
        accessorKey: "id",
        header: "ID",
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.id}</span>,
      },
      { accessorKey: "filename", header: "文件名" },
      {
        accessorKey: "size_bytes",
        header: "大小",
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.size_bytes}</span>,
      },
      {
        accessorKey: "duration_sec",
        header: "时长",
        cell: ({ row }) => dash(row.original.duration_sec),
      },
      {
        id: "res",
        header: "分辨率",
        cell: ({ row }) =>
          row.original.width && row.original.height
            ? `${row.original.width}×${row.original.height}`
            : "—",
      },
      {
        id: "actions",
        header: "操作",
        cell: ({ row }) => (
          <Button
            size="xs"
            variant="destructive"
            onClick={() => deleteVideo.mutate(row.original.id)}
          >
            删除
          </Button>
        ),
      },
    ],
    [deleteVideo],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="摄像头"
        description="已创建的摄像头只能改名称；更换类型或视频源请新建。"
        actions={
          <>
            <Button variant="outline" onClick={() => void camerasQuery.refetch()}>
              刷新
            </Button>
            <Button onClick={() => setCreateOpen(true)}>新建</Button>
          </>
        }
      />

      {camerasQuery.isPending ? (
        <p className="text-sm text-muted-foreground">正在加载摄像头…</p>
      ) : (
        <DataTable
          columns={cameraColumns}
          data={camerasQuery.data ?? []}
          getRowId={(row) => String(row.id)}
          emptyMessage="暂无摄像头，请新建一路。"
        />
      )}

      <div className="space-y-3">
        <h2 className="text-lg font-medium">已上传视频</h2>
        {videosQuery.isPending ? (
          <p className="text-sm text-muted-foreground">正在加载视频…</p>
        ) : (
          <DataTable
            columns={videoColumns}
            data={videosQuery.data ?? []}
            getRowId={(row) => String(row.id)}
            emptyMessage="暂无已上传视频。"
          />
        )}
      </div>

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          setCreateOpen(open);
          if (!open) form.reset();
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>新建摄像头</DialogTitle>
            <DialogDescription>填写名称和源地址。类型与源创建后不可改，请确认后再添加。</DialogDescription>
          </DialogHeader>
          <form
            className="grid gap-3"
            onSubmit={form.handleSubmit((values) => createCamera.mutate(values))}
          >
            <div className="grid gap-1.5">
              <Label htmlFor="c-name">名称</Label>
              <Input id="c-name" placeholder="门口" {...form.register("name")} />
              {form.formState.errors.name ? (
                <p className="text-xs text-destructive">{form.formState.errors.name.message}</p>
              ) : null}
            </div>
            <div className="grid gap-1.5">
              <Label>类型</Label>
              <Controller
                control={form.control}
                name="source_type"
                render={({ field }) => (
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="file">视频文件</SelectItem>
                      <SelectItem value="rtsp">RTSP 流</SelectItem>
                    </SelectContent>
                  </Select>
                )}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="c-uri">源地址</Label>
              <Input
                id="c-uri"
                placeholder="/path/to/video.mp4 或 rtsp://..."
                {...form.register("source_uri")}
              />
              {form.formState.errors.source_uri ? (
                <p className="text-xs text-destructive">{form.formState.errors.source_uri.message}</p>
              ) : null}
            </div>
            {sourceType === "file" ? (
              <div>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="video/*,.mkv,.ts"
                  hidden
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void uploadFile(file);
                    e.target.value = "";
                  }}
                />
                <Button type="button" variant="outline" onClick={() => fileInputRef.current?.click()}>
                  选择文件…
                </Button>
              </div>
            ) : null}
            <Label className="font-normal">
              <Controller
                control={form.control}
                name="autostart"
                render={({ field }) => (
                  <Checkbox
                    checked={field.value}
                    onCheckedChange={(checked) => field.onChange(checked === true)}
                  />
                )}
              />
              创建即启动
            </Label>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setCreateOpen(false)}>
                取消
              </Button>
              <Button type="submit" disabled={createCamera.isPending}>
                添加
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
