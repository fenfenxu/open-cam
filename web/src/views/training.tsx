import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { toast } from "sonner";
import { DataTable, type DataTableColumn } from "@/components/app/data-table";
import { PageHeader } from "@/components/app/page-header";
import { RuleCanvas } from "@/components/app/rule-canvas";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Check, Lock } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, resolveApiUrl } from "@/lib/api";
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
import {
  MODEL_STATUS_NAMES,
  TRAINING_RUN_STATUS_NAMES,
  TRAINING_TASK_STATUS_NAMES,
} from "@/lib/labels";

const NONE = "__none__";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

export function TrainingPage() {
  const pathname = usePathname() || "";
  const parts = pathname.split("/").filter(Boolean);
  const id = parts[0] === "training" ? parts[1] : undefined;
  const router = useRouter();
  const navigate = router.push;
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
  const [maxReachedStep, setMaxReachedStep] = useState(1);
  const syncedTask = useRef<string | null>(null);

  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });
  const videosQuery = useQuery({
    queryKey: ["videos"],
    queryFn: () => api<VideoAsset[]>("/api/videos").catch(() => [] as VideoAsset[]),
  });
  const tasksQuery = useQuery({
    queryKey: ["training-tasks"],
    queryFn: () => api<TrainingTask[]>("/api/training/tasks"),
  });
  const taskQuery = useQuery({
    queryKey: ["training-task", id],
    queryFn: () => api<TrainingTask>(`/api/training/tasks/${id}`),
    enabled: Boolean(id),
  });
  const vlmQuery = useQuery({
    queryKey: ["system-vlm"],
    queryFn: () => api<VlmConfig>("/api/system/vlm"),
    enabled: step === 1,
  });
  const reviewQuery = useQuery({
    queryKey: ["training-review", id],
    queryFn: () => api<ReviewQueue>(`/api/training/tasks/${id}/review`),
    enabled: Boolean(id) && step === 4,
  });
  const trainQuery = useQuery({
    queryKey: ["training-train", id],
    queryFn: () => api<TrainState>(`/api/training/tasks/${id}/train`),
    enabled: Boolean(id) && step === 5,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1500 : false),
  });
  const modelsQuery = useQuery({
    queryKey: ["models", id],
    queryFn: () =>
      api<ModelVersion[]>(`/api/models?task_id=${encodeURIComponent(id!)}`).catch(
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
      setMaxReachedStep(1);
      setGoal("");
      return;
    }
    if (!task || syncedTask.current === task.task_id) return;
    syncedTask.current = task.task_id;
    const inferredStep = inferStep(task);
    setStep(inferredStep);
    setMaxReachedStep(inferredStep);
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
      setMaxReachedStep((current) => Math.max(current, 6));
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

  function goToStep(nextStep: number) {
    if (nextStep > maxReachedStep) {
      setMaxReachedStep(nextStep);
    }
    setStep(nextStep);
  }

  const defineTask = useMutation({
    mutationFn: () =>
      api<TrainingTask>(
        "/api/training/tasks",
        jsonBody("POST", { goal: goal.trim(), task_id: id || undefined }),
      ),
    onSuccess: async (created) => {
      await queryClient.invalidateQueries({ queryKey: ["training-tasks"] });
      syncedTask.current = null;
      navigate(`/training/${created.task_id}`);
      goToStep(2);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const confirmDef = useMutation({
    mutationFn: () =>
      api(`/api/training/tasks/${id}/confirm`, jsonBody("POST", {
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
      goToStep(3);
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
      return api<{ written?: number }>(`/api/training/tasks/${id}/frames`, jsonBody("POST", payload));
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
      api(`/api/training/tasks/${id}/region`, jsonBody("PUT", { region: points })),
    onSuccess: async () => {
      toast.success("区域已保存");
      await refreshTask();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const annotate = useMutation({
    mutationFn: () =>
      api<AnnotateResult>(`/api/training/tasks/${id}/annotate`, { method: "POST" }),
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
        `/api/training/tasks/${id}/review/${body.itemId}`,
        jsonBody("POST", body.action === "skip" ? { action: "skip" } : { action: "confirm", label: body.label }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["training-review", id] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const startTrain = useMutation({
    mutationFn: () =>
      api(`/api/training/tasks/${id}/train`, jsonBody("POST", { epochs: 20 })),
    onSuccess: async () => {
      toast.success("已开始训练");
      await queryClient.invalidateQueries({ queryKey: ["training-train", id] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  async function deploy(modelId: number, force: boolean) {
    const r = await api<{ reason?: string }>(
      `/api/models/${modelId}/deploy`,
      jsonBody("POST", { force }),
    );
    toast.success(r.reason || "已部署");
    await queryClient.invalidateQueries({ queryKey: ["models", id] });
    await refreshTask();
  }

  const registerDeploy = useMutation({
    mutationFn: async (force: boolean) => {
      const m = await api<ModelVersion>("/api/models", jsonBody("POST", { task_id: id }));
      await deploy(m.id, force);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const rollback = useMutation({
    mutationFn: (modelId: number) =>
      api<{ reason?: string }>(`/api/models/${modelId}/rollback`, { method: "POST" }),
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
        header: "编号",
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.id}</span>,
      },
      { accessorKey: "slot_key", header: "槽位" },
      {
        accessorKey: "status",
        header: "状态",
        cell: ({ row }) => (
          <Badge variant="secondary">
            {MODEL_STATUS_NAMES[row.original.status] || row.original.status}
          </Badge>
        ),
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
          `${row.original.object || row.original.task_id} · ${row.original.property || (row.original.status ? TRAINING_TASK_STATUS_NAMES[row.original.status] || row.original.status : "")}`,
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
      ? resolveApiUrl(`/api/training/tasks/${id}/preview.jpg?t=${task?.frames}`)
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

      <div className="rounded-lg border bg-muted/20 px-3 py-4 sm:px-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-medium">训练流程</p>
            <p className="text-xs text-muted-foreground">
              {id ? `第 ${step} 步，共 ${TRAINING_STEPS.length} 步` : "先从一句话需求开始"}
            </p>
          </div>
          {id ? (
            <Badge variant="secondary">
              已完成 {Math.max(0, maxReachedStep - (step <= maxReachedStep ? 1 : 0))} / {TRAINING_STEPS.length}
            </Badge>
          ) : null}
        </div>
        <nav aria-label="训练步骤" className="overflow-x-auto pb-1">
          <ol className="flex min-w-[760px] items-start">
            {TRAINING_STEPS.map((s, index) => {
              const isCurrent = s.id === step;
              const isComplete = s.id < maxReachedStep;
              const isAvailable = s.id <= maxReachedStep;

              return (
                <li key={s.id} className="flex min-w-0 flex-1 items-start">
                  <button
                    type="button"
                    aria-label={`第 ${s.id} 步：${s.title}`}
                    aria-current={isCurrent ? "step" : undefined}
                    aria-disabled={!isAvailable}
                    disabled={!isAvailable}
                    onClick={() => goToStep(s.id)}
                    className={cn(
                      "group flex min-w-[82px] flex-1 flex-col items-center gap-2 rounded-md px-1 text-center outline-none transition-colors",
                      "focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                      !isAvailable && "cursor-not-allowed opacity-55",
                    )}
                  >
                    <span
                      className={cn(
                        "flex size-8 items-center justify-center rounded-full border text-sm font-semibold transition-colors",
                        isCurrent && "border-primary bg-primary text-primary-foreground shadow-sm",
                        isComplete && !isCurrent && "border-primary bg-primary/10 text-primary",
                        !isCurrent && !isComplete && "border-border bg-background text-muted-foreground",
                        isAvailable && !isCurrent && "group-hover:border-primary/60 group-hover:text-primary",
                      )}
                    >
                      {isComplete ? (
                        <Check aria-hidden="true" className="size-4" />
                      ) : isAvailable ? (
                        s.id
                      ) : (
                        <Lock aria-hidden="true" className="size-3.5" />
                      )}
                    </span>
                    <span
                      className={cn(
                        "whitespace-nowrap text-xs font-medium",
                        isCurrent ? "text-foreground" : isComplete ? "text-primary" : "text-muted-foreground",
                      )}
                    >
                      {s.title}
                    </span>
                  </button>
                  {index < TRAINING_STEPS.length - 1 ? (
                    <span
                      aria-hidden="true"
                      className={cn(
                        "mt-4 h-px min-w-3 flex-1 bg-border transition-colors",
                        s.id < maxReachedStep && "bg-primary/60",
                      )}
                    />
                  ) : null}
                </li>
              );
            })}
          </ol>
        </nav>
      </div>

      <div className="space-y-4 rounded-lg border p-4">
        {step === 1 && (
          <>
            <h3 className="text-lg font-medium">① 说需求</h3>
            <p className="text-sm text-muted-foreground">例如：「垃圾桶快满了就提醒我」</p>
            {vlmQuery.data && !vlmQuery.data.configured ? (
              <p className="text-sm text-destructive">
                还没配置大模型。
                <Link prefetch={false} className="underline" href="/settings">
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
              <Button variant="destructive" onClick={() => goToStep(1)}>
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
                    <SelectValue>
                      {cameraSrc === NONE
                        ? "—"
                        : (() => {
                            const camera = cameras.find((item) => String(item.id) === cameraSrc);
                            return camera ? `[${camera.id}] ${camera.name}` : "选择摄像头";
                          })()}
                    </SelectValue>
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
                    <SelectValue>
                      {videoSrc === NONE
                        ? "—"
                        : videos.find((item) => String(item.id) === videoSrc)?.filename || "选择视频"}
                    </SelectValue>
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
              <Button variant="outline" onClick={() => goToStep(4)}>
                下一步：自动标注
              </Button>
              <Button variant="destructive" onClick={() => goToStep(2)}>
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
                  src={resolveApiUrl(`/api/training/tasks/${id}/crop/${reviewItem.id}.jpg`)}
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
              <Button variant="outline" onClick={() => goToStep(5)}>
                下一步：训练
              </Button>
              <Button variant="destructive" onClick={() => goToStep(3)}>
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
                {(TRAINING_RUN_STATUS_NAMES[trainQuery.data?.status || task?.train?.status || "idle"] ||
                  trainQuery.data?.status || task?.train?.status || "idle") +
                  (trainQuery.data?.error ? ` · ${trainQuery.data.error}` : "")}
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={() => goToStep(6)}>
                查看评估
              </Button>
              <Button variant="destructive" onClick={() => goToStep(4)}>
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
              <Button variant="outline" onClick={() => goToStep(7)}>
                去部署
              </Button>
              <Button variant="destructive" onClick={() => goToStep(5)}>
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
            <Button variant="destructive" onClick={() => goToStep(6)}>
              返回
            </Button>
          </>
        ) : null}
      </div>
    </div>
  );
}
