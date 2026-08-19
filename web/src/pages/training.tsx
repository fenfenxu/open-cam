import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { toast } from "sonner";
import { DataTable, type DataTableColumn } from "@/components/app/data-table";
import { PageHeader } from "@/components/app/page-header";
import { RuleCanvas } from "@/components/app/rule-canvas";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { jsonBody, type Camera, type ModelVersion, type VideoAsset } from "@/lib/cameras";
import type { Point } from "@/lib/rules";
import {
  TRAINING_STEPS,
  inferStep,
  type AnnotateResult,
  type ReviewQueue,
  type TrainState,
  type TrainingTask,
} from "@/lib/training";
import { cn } from "@/lib/utils";
import type { VlmConfig } from "@/lib/system";

const NONE = "__none__";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

export function TrainingPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [step, setStep] = useState(1);
  const [goal, setGoal] = useState("");
  const [object, setObject] = useState("");
  const [property, setProperty] = useState("");
  const [classesText, setClassesText] = useState("");
  const [trigger, setTrigger] = useState("");
  const [cameraSrc, setCameraSrc] = useState(NONE);
  const [videoSrc, setVideoSrc] = useState(NONE);
  const [points, setPoints] = useState<Point[]>([]);
  const [canvasReady, setCanvasReady] = useState(false);
  const syncedTask = useRef<string | null>(null);

  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/cameras"),
  });
  const videosQuery = useQuery({
    queryKey: ["videos"],
    queryFn: () => api<VideoAsset[]>("/videos").catch(() => [] as VideoAsset[]),
  });
  const tasksQuery = useQuery({
    queryKey: ["training-tasks"],
    queryFn: () => api<TrainingTask[]>("/training/tasks"),
  });
  const taskQuery = useQuery({
    queryKey: ["training-task", id],
    queryFn: () => api<TrainingTask>(`/training/tasks/${id}`),
    enabled: Boolean(id),
  });
  const vlmQuery = useQuery({
    queryKey: ["system-vlm"],
    queryFn: () => api<VlmConfig>("/api/system/vlm"),
    enabled: step === 1,
  });
  const reviewQuery = useQuery({
    queryKey: ["training-review", id],
    queryFn: () => api<ReviewQueue>(`/training/tasks/${id}/review`),
    enabled: Boolean(id) && step === 4,
  });
  const trainQuery = useQuery({
    queryKey: ["training-train", id],
    queryFn: () => api<TrainState>(`/training/tasks/${id}/train`),
    enabled: Boolean(id) && step === 5,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
  });
  const modelsQuery = useQuery({
    queryKey: ["models", id],
    queryFn: () =>
      api<ModelVersion[]>(`/models?task_id=${encodeURIComponent(id!)}`).catch(
        () => [] as ModelVersion[],
      ),
    enabled: Boolean(id) && step === 7,
  });

  const task = id ? taskQuery.data : undefined;
  const cameras = camerasQuery.data ?? [];
  const videos = videosQuery.data ?? [];
  const tasks = tasksQuery.data ?? [];

  useEffect(() => {
    if (tasksQuery.isError) toast.error(errorMessage(tasksQuery.error));
  }, [tasksQuery.isError, tasksQuery.error]);

  useEffect(() => {
    if (taskQuery.isError) toast.error(errorMessage(taskQuery.error));
  }, [taskQuery.isError, taskQuery.error]);

  useEffect(() => {
    if (!id) {
      syncedTask.current = null;
      setStep(1);
      setGoal("");
      return;
    }
    if (!task || syncedTask.current === task.task_id) return;
    syncedTask.current = task.task_id;
    setStep(inferStep(task));
    setGoal(task.goal || "");
    const d = task.definition || {};
    setObject(d.object || "");
    setProperty(d.property || "");
    setClassesText((d.classes || []).join(", "));
    setTrigger(d.rule?.trigger || "");
    setPoints([]);
  }, [id, task]);

  useEffect(() => {
    if (trainQuery.data?.status === "done") {
      void queryClient.invalidateQueries({ queryKey: ["training-task", id] });
      setStep(6);
    }
    if (trainQuery.data?.status === "failed") {
      toast.error(trainQuery.data.error || "训练失败");
    }
  }, [trainQuery.data, id, queryClient]);

  async function refreshTask() {
    await queryClient.invalidateQueries({ queryKey: ["training-task", id] });
    await queryClient.invalidateQueries({ queryKey: ["training-tasks"] });
  }

  const defineTask = useMutation({
    mutationFn: () =>
      api<TrainingTask>(
        "/training/tasks",
        jsonBody("POST", { goal: goal.trim(), task_id: id || undefined }),
      ),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["training-tasks"] });
      syncedTask.current = null;
      navigate(`/training/${created.task_id}`);
      setStep(2);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const confirmDef = useMutation({
    mutationFn: () =>
      api(`/training/tasks/${id}/confirm`, jsonBody("POST", {
        definition: {
          object: object.trim(),
          property: property.trim(),
          classes: classesText
            .split(/[,，]/)
            .map((s) => s.trim())
            .filter(Boolean),
          rule: { type: "state_alert", trigger: trigger.trim() },
          metrics: task?.definition?.metrics,
          region: task?.definition?.region,
          goal: task?.goal,
        },
      })),
    onSuccess: async () => {
      await refreshTask();
      setStep(3);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const extractFrames = useMutation({
    mutationFn: () => {
      const payload =
        cameraSrc !== NONE
          ? { camera_id: Number(cameraSrc) }
          : videoSrc !== NONE
            ? { video_id: Number(videoSrc) }
            : null;
      if (!payload) throw new Error("请选择摄像头或视频");
      return api<{ written?: number }>(`/training/tasks/${id}/frames`, jsonBody("POST", payload));
    },
    onSuccess: async (r) => {
      toast.success(`抽了 ${r.written ?? 0} 帧`);
      setPoints([]);
      await refreshTask();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const saveRegion = useMutation({
    mutationFn: () =>
      api(`/training/tasks/${id}/region`, jsonBody("PUT", { region: points })),
    onSuccess: async () => {
      toast.success("区域已保存");
      await refreshTask();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const annotate = useMutation({
    mutationFn: () =>
      api<AnnotateResult>(`/training/tasks/${id}/annotate`, { method: "POST" }),
    onSuccess: async (r) => {
      toast.success(`自动 ${r.auto}，待确认 ${r.review}`);
      await refreshTask();
      await queryClient.invalidateQueries({ queryKey: ["training-review", id] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const reviewAction = useMutation({
    mutationFn: (body: { action: string; label?: string; itemId: string }) =>
      api(
        `/training/tasks/${id}/review/${body.itemId}`,
        jsonBody("POST", body.action === "skip" ? { action: "skip" } : { action: "confirm", label: body.label }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["training-review", id] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const startTrain = useMutation({
    mutationFn: () =>
      api(`/training/tasks/${id}/train`, jsonBody("POST", { epochs: 20 })),
    onSuccess: async () => {
      toast.success("已开始训练");
      await queryClient.invalidateQueries({ queryKey: ["training-train", id] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  async function deploy(modelId: number, force: boolean) {
    const r = await api<{ reason?: string }>(
      `/models/${modelId}/deploy`,
      jsonBody("POST", { force }),
    );
    toast.success(r.reason || "已部署");
    await queryClient.invalidateQueries({ queryKey: ["models", id] });
    await refreshTask();
  }

  const registerDeploy = useMutation({
    mutationFn: async (force: boolean) => {
      const m = await api<ModelVersion>("/models", jsonBody("POST", { task_id: id }));
      await deploy(m.id, force);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const rollback = useMutation({
    mutationFn: (modelId: number) =>
      api<{ reason?: string }>(`/models/${modelId}/rollback`, { method: "POST" }),
    onSuccess: async (r) => {
      toast.success(r.reason || "已回滚");
      await queryClient.invalidateQueries({ queryKey: ["models", id] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const modelColumns = useMemo<DataTableColumn<ModelVersion>[]>(
    () => [
      {
        accessorKey: "id",
        header: "id",
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.id}</span>,
      },
      { accessorKey: "slot_key", header: "槽位" },
      {
        accessorKey: "status",
        header: "状态",
        cell: ({ row }) => <Badge variant="secondary">{row.original.status}</Badge>,
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) =>
          row.original.status === "live" ? (
            <Button size="xs" variant="outline" onClick={() => rollback.mutate(row.original.id)}>
              回滚
            </Button>
          ) : (
            <Button
              size="xs"
              variant="outline"
              onClick={() => void deploy(row.original.id, false).catch((err) => toast.error(errorMessage(err)))}
            >
              部署
            </Button>
          ),
      },
    ],
    [rollback],
  );

  const taskColumns = useMemo<DataTableColumn<TrainingTask>[]>(
    () => [
      {
        accessorKey: "task_id",
        header: "任务",
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.task_id}</span>
        ),
      },
      {
        id: "label",
        header: "对象 / 属性",
        cell: ({ row }) =>
          `${row.original.object || row.original.task_id} · ${row.original.property || row.original.status || ""}`,
      },
      {
        id: "open",
        header: "",
        cell: ({ row }) => (
          <Button
            size="xs"
            variant="outline"
            onClick={() => navigate(`/training/${row.original.task_id}`)}
          >
            打开
          </Button>
        ),
      },
    ],
    [navigate],
  );

  const previewUrl =
    id && (task?.frames || 0) > 0
      ? `/training/tasks/${id}/preview.jpg?t=${task?.frames}`
      : null;
  const reviewItem = reviewQuery.data?.items[0];
  const report = task?.train?.result || {};

  return (
    <div className="space-y-6">
      <PageHeader
        title="模型训练"
        description="用一句话描述想监控的状态，系统帮你抽帧、标注、训练并部署本地小模型。"
        actions={
          <Button variant="outline" onClick={() => navigate("/training")}>
            新任务
          </Button>
        }
      />

      <div className="space-y-2">
        <h2 className="text-sm font-medium">已有任务</h2>
        {tasksQuery.isPending ? (
          <p className="text-sm text-muted-foreground">正在加载任务…</p>
        ) : (
          <DataTable
            columns={taskColumns}
            data={tasks}
            getRowId={(row) => row.task_id}
            emptyMessage="还没有训练任务。"
          />
        )}
      </div>

      <div className="flex flex-wrap gap-1">
        {TRAINING_STEPS.map((s) => (
          <Button
            key={s.id}
            size="xs"
            variant={s.id === step ? "default" : "outline"}
            className={cn(s.id === step && "pointer-events-none")}
            onClick={() => setStep(s.id)}
          >
            {s.id}. {s.title}
          </Button>
        ))}
      </div>

      <div className="space-y-4 rounded-lg border p-4">
        {step === 1 && (
          <>
            <h3 className="text-lg font-medium">① 说需求</h3>
            <p className="text-sm text-muted-foreground">例如：「垃圾桶快满了就提醒我」</p>
            {vlmQuery.data && !vlmQuery.data.configured ? (
              <p className="text-sm text-destructive">
                还没配置大模型。
                <Link className="underline" to="/settings">
                  去设置页填写接口和 API Key
                </Link>
                ，否则系统无法真正理解你写的需求。
              </p>
            ) : null}
            <Textarea
              id="goal"
              rows={3}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
            />
            <Button
              onClick={() => {
                if (!goal.trim()) {
                  toast.error("请先写一句需求");
                  return;
                }
                defineTask.mutate();
              }}
              disabled={defineTask.isPending}
            >
              生成任务定义
            </Button>
          </>
        )}

        {step !== 1 && !id ? (
          <p className="text-sm text-muted-foreground">请先完成第一步。</p>
        ) : null}

        {step === 2 && id ? (
          <>
            <h3 className="text-lg font-medium">② 确认定义</h3>
            {task?.metrics_explained ? (
              <p className="text-sm text-muted-foreground">{task.metrics_explained}</p>
            ) : null}
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <Label htmlFor="d-object">对象</Label>
                <Input id="d-object" value={object} onChange={(e) => setObject(e.target.value)} />
              </div>
              <div className="grid gap-1.5">
                <Label htmlFor="d-property">属性</Label>
                <Input id="d-property" value={property} onChange={(e) => setProperty(e.target.value)} />
              </div>
              <div className="grid gap-1.5 sm:col-span-2">
                <Label htmlFor="d-classes">类别（逗号分隔）</Label>
                <Input
                  id="d-classes"
                  value={classesText}
                  onChange={(e) => setClassesText(e.target.value)}
                />
              </div>
              <div className="grid gap-1.5 sm:col-span-2">
                <Label htmlFor="d-trigger">告警触发</Label>
                <Input id="d-trigger" value={trigger} onChange={(e) => setTrigger(e.target.value)} />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button onClick={() => confirmDef.mutate()} disabled={confirmDef.isPending}>
                确认并进入下一步
              </Button>
              <Button variant="destructive" onClick={() => setStep(1)}>
                返回改需求
              </Button>
            </div>
          </>
        ) : null}

        {step === 3 && id ? (
          <>
            <h3 className="text-lg font-medium">③ 选视频源并抽帧</h3>
            <div className="flex flex-wrap items-end gap-3">
              <div className="grid gap-1.5">
                <Label>摄像头</Label>
                <Select value={cameraSrc} onValueChange={(v) => v && setCameraSrc(String(v))}>
                  <SelectTrigger className="w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>—</SelectItem>
                    {cameras.map((c) => (
                      <SelectItem key={c.id} value={String(c.id)}>
                        [{c.id}] {c.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-1.5">
                <Label>或视频库</Label>
                <Select value={videoSrc} onValueChange={(v) => v && setVideoSrc(String(v))}>
                  <SelectTrigger className="w-56">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={NONE}>—</SelectItem>
                    {videos.map((v) => (
                      <SelectItem key={v.id} value={String(v.id)}>
                        {v.filename}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button onClick={() => extractFrames.mutate()} disabled={extractFrames.isPending}>
                抽帧
              </Button>
            </div>
            <p className="text-sm text-muted-foreground">
              已抽 {task?.frames || 0} 张。抽帧后可在画面上点出垃圾桶所在区域。
            </p>
            {previewUrl ? (
              <RuleCanvas
                snapshotUrl={previewUrl}
                existing={[]}
                points={points}
                zoneShape="polygon"
                onPointsChange={setPoints}
                onReady={setCanvasReady}
              />
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button
                disabled={!task?.frames || points.length < 3 || !canvasReady || saveRegion.isPending}
                onClick={() => {
                  if (points.length < 3) {
                    toast.error("至少点 3 个顶点");
                    return;
                  }
                  saveRegion.mutate();
                }}
              >
                保存区域
              </Button>
              <Button variant="outline" onClick={() => setStep(4)}>
                下一步：自动标注
              </Button>
              <Button variant="destructive" onClick={() => setStep(2)}>
                返回
              </Button>
            </div>
          </>
        ) : null}

        {step === 4 && id ? (
          <>
            <h3 className="text-lg font-medium">④ 自动标注 + 人工确认</h3>
            <p className="text-sm text-muted-foreground">
              高置信样本自动入库；不确定的只需点类别或跳过。
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={() => annotate.mutate()} disabled={annotate.isPending}>
                开始标注
              </Button>
              <span className="text-sm text-muted-foreground">
                待确认 {reviewQuery.data?.remaining ?? "—"} 张
              </span>
            </div>
            {reviewItem ? (
              <div className="space-y-2">
                <img
                  className="max-h-64 rounded-md border object-contain"
                  src={`/training/tasks/${id}/crop/${reviewItem.id}.jpg`}
                  alt="裁剪"
                />
                <p className="text-sm text-muted-foreground">
                  建议：{reviewItem.suggested_label || "无"}（{reviewItem.confidence.toFixed(2)}）{" "}
                  {reviewItem.reason || ""}
                </p>
                <div className="flex flex-wrap gap-2">
                  {reviewItem.classes.map((c) => (
                    <Button
                      key={c}
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        reviewAction.mutate({ action: "confirm", label: c, itemId: reviewItem.id })
                      }
                    >
                      {c}
                    </Button>
                  ))}
                  <Button
                    size="sm"
                    variant="destructive"
                    onClick={() => reviewAction.mutate({ action: "skip", itemId: reviewItem.id })}
                  >
                    跳过
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">队列已空，可以去训练。</p>
            )}
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => setStep(5)}>
                下一步：训练
              </Button>
              <Button variant="destructive" onClick={() => setStep(3)}>
                返回
              </Button>
            </div>
          </>
        ) : null}

        {step === 5 && id ? (
          <>
            <h3 className="text-lg font-medium">⑤ 训练</h3>
            <p className="text-sm text-muted-foreground">
              从预训练 YOLO 微调固定区域分类。训练在后台执行。
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={() => startTrain.mutate()} disabled={startTrain.isPending}>
                开始训练
              </Button>
              <span className="text-sm text-muted-foreground">
                {(trainQuery.data?.status || task?.train?.status || "idle") +
                  (trainQuery.data?.error ? ` · ${trainQuery.data.error}` : "")}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => setStep(6)}>
                查看评估
              </Button>
              <Button variant="destructive" onClick={() => setStep(4)}>
                返回
              </Button>
            </div>
          </>
        ) : null}

        {step === 6 && id ? (
          <>
            <h3 className="text-lg font-medium">⑥ 评估报告</h3>
            <p className="text-sm">{report.conclusion || "还没有评估报告，请先完成训练。"}</p>
            {report.suggestions?.length ? (
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {report.suggestions.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => setStep(7)}>
                去部署
              </Button>
              <Button variant="destructive" onClick={() => setStep(5)}>
                返回
              </Button>
            </div>
          </>
        ) : null}

        {step === 7 && id ? (
          <>
            <h3 className="text-lg font-medium">⑦ 一键部署 / 回滚</h3>
            <p className="text-sm text-muted-foreground">
              登记本任务最新模型，与线上指标对比后再替换；回滚入口常驻。
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                onClick={() => registerDeploy.mutate(false)}
                disabled={registerDeploy.isPending}
              >
                登记并部署
              </Button>
              <Button
                variant="destructive"
                onClick={() => registerDeploy.mutate(true)}
                disabled={registerDeploy.isPending}
              >
                强制部署
              </Button>
            </div>
            {modelsQuery.isPending ? (
              <p className="text-sm text-muted-foreground">正在加载模型…</p>
            ) : (
              <DataTable
                columns={modelColumns}
                data={modelsQuery.data ?? []}
                getRowId={(row) => String(row.id)}
                emptyMessage="还没有登记模型。"
              />
            )}
            <Button variant="destructive" onClick={() => setStep(6)}>
              返回
            </Button>
          </>
        ) : null}
      </div>
    </div>
  );
}
