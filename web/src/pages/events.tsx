import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Star } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { DataTable, type DataTableColumn } from "@/components/app/data-table";
import { DetailDrawer } from "@/components/app/detail-drawer";
import { PageHeader } from "@/components/app/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { api, ApiError, fmtTime } from "@/lib/api";
import {
  ACTION_NAMES,
  NEXT_ACTIONS,
  RULE_TYPE_NAMES,
  STATUS_NAMES,
  VLM_VERDICT_NAMES,
} from "@/lib/labels";

const ALL = "__all__";

type Camera = { id: number; name: string };

type CamEvent = {
  id: number;
  camera_id: number;
  type: string;
  confidence: number;
  ts: number;
  snapshot_path: string | null;
  source_offset: number | null;
  camera_name?: string | null;
  source_filename?: string | null;
  detail: Record<string, unknown>;
  vlm_status: string;
  vlm_verdict: string | null;
  vlm_reason: string | null;
  status: string;
  starred: boolean;
  assignee: string | null;
  note: string | null;
  needs_action: boolean;
  clip_start?: number | null;
  clip_end?: number | null;
};

type EventAction = {
  id: number;
  action: string;
  actor: string;
  payload: Record<string, unknown>;
  ts: number;
};

type TrainingTask = {
  task_id: string;
  object: string;
  property: string;
  status: string;
};

type Filters = {
  cameraId: string;
  ruleType: string;
  status: string;
  verdict: string;
  starred: boolean;
  includeObserve: boolean;
};

function jsonBody(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

function cameraLabel(e: CamEvent): string {
  return e.camera_name || `#${e.camera_id}`;
}

function sourceLabel(e: CamEvent): string {
  if (e.source_filename) return e.source_filename;
  return e.source_offset == null ? "直播流（无回放）" : "—";
}

function fmtMediaTime(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(Number(sec))) return "—";
  const s = Math.max(0, Number(sec));
  const m = Math.floor(s / 60);
  const rest = (s - m * 60).toFixed(2).padStart(5, "0");
  return `${String(m).padStart(2, "0")}:${rest}`;
}

function fmtClipRange(event: CamEvent): string {
  if (event.source_offset == null) return "—";
  return `${fmtMediaTime(event.clip_start)} – ${fmtMediaTime(event.clip_end)}`;
}

function fmtPayload(a: EventAction): string {
  const p = a.payload || {};
  if (a.action === "notify") return p.ok ? "推送成功" : `失败：${p.error || ""}`;
  if (a.action === "status") {
    const from = String(p.from ?? "");
    const to = String(p.to ?? "");
    return `${STATUS_NAMES[from] || from} → ${STATUS_NAMES[to] || to}`;
  }
  if (a.action === "assign") return `→ ${p.to || "（取消指派）"}`;
  if (a.action === "note") return String(p.text || "");
  return "";
}

function FilterSelect({
  value,
  onChange,
  items,
}: {
  value: string;
  onChange: (value: string) => void;
  items: { value: string; label: string }[];
}) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next ?? ALL)}>
      <SelectTrigger>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            {item.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function EventClipPlayer({ event }: { event: CamEvent }) {
  const [failed, setFailed] = useState(false);
  const start = event.clip_start ?? 0;
  const end = event.clip_end ?? 0;

  if (event.source_offset == null) {
    return (
      <p className="text-sm text-muted-foreground">
        该事件没有可回放的视频片段（实时流或升级前的旧数据只有快照）。
      </p>
    );
  }
  if (failed) {
    return (
      <p className="text-sm text-muted-foreground">
        浏览器无法播放该素材格式，请查看上方带时段标注的快照。
      </p>
    );
  }

  return (
    <video
      className="w-full rounded-md bg-black"
      controls
      autoPlay
      playsInline
      src={`/events/${event.id}/clip`}
      onLoadedMetadata={(ev) => {
        const video = ev.currentTarget;
        if (video.duration > end - start + 1.5) video.currentTime = start;
      }}
      onTimeUpdate={(ev) => {
        const video = ev.currentTarget;
        if (video.duration > end - start + 1.5 && video.currentTime >= end) video.pause();
      }}
      onError={() => setFailed(true)}
    />
  );
}

function Kv({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[5.5rem_1fr] gap-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-all">{children}</dd>
    </div>
  );
}

export function EventsPage() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<Filters>({
    cameraId: ALL,
    ruleType: ALL,
    status: ALL,
    verdict: ALL,
    starred: false,
    includeObserve: false,
  });
  const [openId, setOpenId] = useState<number | null>(null);
  const [assignee, setAssignee] = useState("");
  const [note, setNote] = useState("");
  const [feedbackTask, setFeedbackTask] = useState("");

  const cameras = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/cameras"),
  });

  const eventsQuery = useQuery({
    queryKey: ["events", filters],
    queryFn: () => {
      const params = new URLSearchParams();
      if (filters.cameraId !== ALL) params.set("camera_id", filters.cameraId);
      if (filters.ruleType !== ALL) params.set("rule_type", filters.ruleType);
      if (filters.status !== ALL) params.set("status", filters.status);
      if (filters.verdict !== ALL) params.set("vlm_verdict", filters.verdict);
      if (filters.starred) params.set("starred", "true");
      if (!filters.includeObserve) params.set("needs_action", "true");
      params.set("limit", "100");
      return api<CamEvent[]>(`/events?${params}`);
    },
  });

  const detailQuery = useQuery({
    queryKey: ["event", openId],
    queryFn: () => api<CamEvent>(`/events/${openId}`),
    enabled: openId != null,
  });

  const actionsQuery = useQuery({
    queryKey: ["event-actions", openId],
    queryFn: () => api<EventAction[]>(`/events/${openId}/actions`),
    enabled: openId != null,
  });

  const tasksQuery = useQuery({
    queryKey: ["training-tasks"],
    queryFn: () => api<TrainingTask[]>("/training/tasks"),
    enabled: openId != null,
  });

  useEffect(() => {
    if (detailQuery.data) {
      setAssignee(detailQuery.data.assignee ?? "");
      setNote(detailQuery.data.note ?? "");
    }
  }, [detailQuery.data]);

  useEffect(() => {
    const confirmed = (tasksQuery.data ?? []).filter((t) => t.status === "confirmed");
    setFeedbackTask(confirmed[0]?.task_id ?? "");
  }, [tasksQuery.data]);

  useEffect(() => {
    if (eventsQuery.isError) toast.error(errorMessage(eventsQuery.error));
  }, [eventsQuery.isError, eventsQuery.error]);

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["events"] });
    await queryClient.invalidateQueries({ queryKey: ["event", openId] });
    await queryClient.invalidateQueries({ queryKey: ["event-actions", openId] });
  };

  const patchEvent = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: number;
      body: Record<string, unknown>;
      msg: string;
    }) => api(`/events/${id}`, jsonBody("PATCH", body)),
    onSuccess: async (_data, vars) => {
      toast.success(vars.msg);
      await invalidate();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const starEvent = useMutation({
    mutationFn: ({ id, starred }: { id: number; starred: boolean }) =>
      api(`/events/${id}`, jsonBody("PATCH", { starred })),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["events"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const notifyEvent = useMutation({
    mutationFn: (id: number) => api(`/events/${id}/notify`, { method: "POST" }),
    onSuccess: () => {
      toast.success("已提交重发，稍后查看处置时间线");
      window.setTimeout(() => {
        void queryClient.invalidateQueries({ queryKey: ["event-actions", openId] });
      }, 1500);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const feedback = useMutation({
    mutationFn: ({ id, kind, taskId }: { id: number; kind: string; taskId: string }) =>
      api(`/events/${id}/feedback`, jsonBody("POST", { task_id: taskId, kind })),
    onSuccess: async (_data, vars) => {
      toast.success(vars.kind === "miss" ? "已记为漏报并入库" : "已记为误报并入库");
      await invalidate();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const columns = useMemo<DataTableColumn<CamEvent>[]>(
    () => [
      {
        id: "star",
        header: "",
        cell: ({ row }) => {
          const e = row.original;
          return (
            <Button
              variant="ghost"
              size="icon-xs"
              title="关注"
              onClick={(ev) => {
                ev.stopPropagation();
                starEvent.mutate({ id: e.id, starred: !e.starred });
              }}
            >
              <Star className={e.starred ? "fill-current" : ""} />
            </Button>
          );
        },
      },
      {
        accessorKey: "ts",
        header: "时间",
        cell: ({ row }) => <span className="font-mono text-xs">{fmtTime(row.original.ts)}</span>,
      },
      {
        id: "camera",
        header: "摄像头",
        cell: ({ row }) => (
          <div>
            <div>{cameraLabel(row.original)}</div>
            <div className="text-xs text-muted-foreground">{sourceLabel(row.original)}</div>
          </div>
        ),
      },
      {
        id: "clip",
        header: "素材",
        cell: ({ row }) => (
          <span className="font-mono text-xs">{fmtClipRange(row.original)}</span>
        ),
      },
      {
        accessorKey: "type",
        header: "类型",
        cell: ({ row }) => RULE_TYPE_NAMES[row.original.type] || row.original.type,
      },
      {
        accessorKey: "confidence",
        header: "置信度",
        cell: ({ row }) => (
          <span className="font-mono text-xs">{row.original.confidence.toFixed(2)}</span>
        ),
      },
      {
        id: "vlm",
        header: "VLM 判定",
        cell: ({ row }) => (
          <Badge variant="secondary">
            {row.original.vlm_verdict || row.original.vlm_status}
          </Badge>
        ),
      },
      {
        accessorKey: "status",
        header: "处置状态",
        cell: ({ row }) => (
          <Badge variant="outline">
            {STATUS_NAMES[row.original.status] || row.original.status}
          </Badge>
        ),
      },
      {
        accessorKey: "assignee",
        header: "负责人",
        cell: ({ row }) => row.original.assignee || "—",
      },
    ],
    [starEvent],
  );

  const event = detailQuery.data;
  const actionable = event?.needs_action !== false;
  const confirmedTasks = (tasksQuery.data ?? []).filter((t) => t.status === "confirmed");

  return (
    <div className="space-y-4">
      <PageHeader
        title="待办"
        description="筛选告警事件，点行在抽屉里处置。观察记录默认不进入待办。"
        actions={
          <Button variant="outline" onClick={() => void eventsQuery.refetch()}>
            刷新
          </Button>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <FilterSelect
          value={filters.cameraId}
          onChange={(cameraId) => setFilters((f) => ({ ...f, cameraId }))}
          items={[
            { value: ALL, label: "全部摄像头" },
            ...(cameras.data ?? []).map((c) => ({
              value: String(c.id),
              label: `[${c.id}] ${c.name}`,
            })),
          ]}
        />
        <FilterSelect
          value={filters.ruleType}
          onChange={(ruleType) => setFilters((f) => ({ ...f, ruleType }))}
          items={[
            { value: ALL, label: "全部类型" },
            ...Object.entries(RULE_TYPE_NAMES).map(([value, label]) => ({ value, label })),
          ]}
        />
        <FilterSelect
          value={filters.status}
          onChange={(status) => setFilters((f) => ({ ...f, status }))}
          items={[
            { value: ALL, label: "全部状态" },
            ...Object.entries(STATUS_NAMES).map(([value, label]) => ({ value, label })),
          ]}
        />
        <FilterSelect
          value={filters.verdict}
          onChange={(verdict) => setFilters((f) => ({ ...f, verdict }))}
          items={[
            { value: ALL, label: "全部判定" },
            ...Object.entries(VLM_VERDICT_NAMES).map(([value, label]) => ({ value, label })),
          ]}
        />
        <Label className="font-normal">
          <Checkbox
            checked={filters.starred}
            onCheckedChange={(checked) =>
              setFilters((f) => ({ ...f, starred: checked === true }))
            }
          />
          仅看关注
        </Label>
        <Label className="font-normal">
          <Checkbox
            checked={filters.includeObserve}
            onCheckedChange={(checked) =>
              setFilters((f) => ({ ...f, includeObserve: checked === true }))
            }
          />
          含观察记录
        </Label>
      </div>

      {eventsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">正在加载事件…</p>
      ) : (
        <DataTable
          columns={columns}
          data={eventsQuery.data ?? []}
          getRowId={(row) => String(row.id)}
          onRowClick={(row) => setOpenId(row.id)}
        />
      )}

      <DetailDrawer
        open={openId != null}
        onOpenChange={(open) => {
          if (!open) setOpenId(null);
        }}
        title={
          event ? (
            <span className="flex items-center gap-2">
              事件 #{event.id}
              <Badge variant="outline">{STATUS_NAMES[event.status] || event.status}</Badge>
            </span>
          ) : (
            "事件详情"
          )
        }
      >
        {detailQuery.isPending && <p className="text-sm text-muted-foreground">加载中…</p>}
        {detailQuery.isError && (
          <p className="text-sm text-destructive">{errorMessage(detailQuery.error)}</p>
        )}
        {event && (
          <>
            <div className="space-y-2">
              {event.snapshot_path ? (
                <img
                  src={`/events/${event.id}/snapshot`}
                  alt="快照"
                  className="w-full rounded-md border"
                />
              ) : null}
              <p className="text-xs text-muted-foreground">
                素材 {fmtClipRange(event)}
                {event.source_offset == null
                  ? ""
                  : `（命中 ${fmtMediaTime(event.source_offset)}）`}
              </p>
              <EventClipPlayer event={event} />
            </div>

            <dl className="space-y-2">
              <Kv label="摄像头">
                <Link className="underline" to={`/cameras/${event.camera_id}`}>
                  {cameraLabel(event)}
                </Link>
              </Kv>
              <Kv label="视频">{sourceLabel(event)}</Kv>
              <Kv label="素材时段">{fmtClipRange(event)}</Kv>
              <Kv label="时间">{fmtTime(event.ts)}</Kv>
              <Kv label="类型">{RULE_TYPE_NAMES[event.type] || event.type}</Kv>
              <Kv label="置信度">{event.confidence}</Kv>
              <Kv label="详情">{JSON.stringify(event.detail)}</Kv>
              <Kv label="VLM 状态">{event.vlm_status}</Kv>
              <Kv label="VLM 判定">{event.vlm_verdict || "—"}</Kv>
              <Kv label="VLM 理由">{event.vlm_reason || "—"}</Kv>
            </dl>

            <h2 className="text-base font-medium">处置</h2>
            {actionable ? (
              <div className="space-y-3">
                <div className="flex flex-wrap gap-2">
                  {(NEXT_ACTIONS[event.status] || []).map(([st, label]) => (
                    <Button
                      key={st}
                      size="sm"
                      onClick={() =>
                        patchEvent.mutate({
                          id: event.id,
                          body: { status: st },
                          msg: "状态已更新",
                        })
                      }
                    >
                      {label}
                    </Button>
                  ))}
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => notifyEvent.mutate(event.id)}
                  >
                    重发通知
                  </Button>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Label htmlFor="d-assignee">负责人</Label>
                  <Input
                    id="d-assignee"
                    className="max-w-xs"
                    value={assignee}
                    placeholder="处置负责人"
                    onChange={(e) => setAssignee(e.target.value)}
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      patchEvent.mutate({
                        id: event.id,
                        body: { assignee: assignee || null },
                        msg: "负责人已保存",
                      })
                    }
                  >
                    保存
                  </Button>
                </div>
                <div className="flex flex-wrap items-start gap-2">
                  <Label htmlFor="d-note" className="mt-2">
                    备注
                  </Label>
                  <Textarea
                    id="d-note"
                    className="min-h-16 flex-1"
                    rows={2}
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() =>
                      patchEvent.mutate({
                        id: event.id,
                        body: { note: note || null },
                        msg: "备注已保存",
                      })
                    }
                  >
                    保存
                  </Button>
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">观察记录不进入待办，无需处置。</p>
            )}

            <h2 className="text-base font-medium">处置时间线</h2>
            {(actionsQuery.data ?? []).length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无处置记录。</p>
            ) : (
              <DataTable
                columns={[
                  {
                    accessorKey: "ts",
                    header: "时间",
                    cell: ({ row }) => (
                      <span className="font-mono text-xs">{fmtTime(row.original.ts)}</span>
                    ),
                  },
                  {
                    accessorKey: "action",
                    header: "操作",
                    cell: ({ row }) => ACTION_NAMES[row.original.action] || row.original.action,
                  },
                  { accessorKey: "actor", header: "操作者" },
                  {
                    id: "payload",
                    header: "细节",
                    cell: ({ row }) => (
                      <span className="font-mono text-xs">{fmtPayload(row.original)}</span>
                    ),
                  },
                ]}
                data={actionsQuery.data ?? []}
                emptyMessage="暂无处置记录。"
              />
            )}

            <div className="space-y-2 rounded-lg border p-3">
              <h3 className="text-sm font-medium">训练反馈</h3>
              <p className="text-xs text-muted-foreground">
                误报/漏报样本会自动进入对应任务的数据集，下次训练会用上。
              </p>
              <Select value={feedbackTask || ALL} onValueChange={(v) => setFeedbackTask(v === ALL ? "" : v ?? "")}>
                <SelectTrigger className="w-full max-w-sm">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {confirmedTasks.length === 0 ? (
                    <SelectItem value={ALL} disabled>
                      {tasksQuery.isError ? "无法加载训练任务" : "没有已确认的训练任务"}
                    </SelectItem>
                  ) : (
                    confirmedTasks.map((t) => (
                      <SelectItem key={t.task_id} value={t.task_id}>
                        {t.object} · {t.property} ({t.task_id})
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if (!feedbackTask) {
                      toast.error("请先选一个训练任务");
                      return;
                    }
                    feedback.mutate({ id: event.id, kind: "false_alarm", taskId: feedbackTask });
                  }}
                >
                  这是误报
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if (!feedbackTask) {
                      toast.error("请先选一个训练任务");
                      return;
                    }
                    feedback.mutate({ id: event.id, kind: "miss", taskId: feedbackTask });
                  }}
                >
                  这是漏报
                </Button>
              </div>
            </div>
          </>
        )}
      </DetailDrawer>
    </div>
  );
}
