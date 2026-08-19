import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
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
import { api, ApiError } from "@/lib/api";
import { jsonBody, type Camera } from "@/lib/cameras";
import { RULE_TYPE_NAMES } from "@/lib/labels";
import {
  DIRECTION_NAMES,
  buildRuleParams,
  defaultFieldValues,
  ruleDisplayName,
  ruleParamSummary,
  type CameraRule,
  type Point,
  type RuleField,
  type RulePreset,
  type RulePresetsResponse,
} from "@/lib/rules";
import { cn } from "@/lib/utils";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

function fieldValue(values: Record<string, unknown>, field: RuleField): string {
  const raw = values[field.key];
  if (field.kind === "class") {
    if (Array.isArray(raw) && raw.length) return String(raw[0]);
    return "person";
  }
  if (raw == null) return "";
  return String(raw);
}

export function RulesPage() {
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const [step, setStep] = useState(1);
  const [preset, setPreset] = useState<RulePreset | null>(null);
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [points, setPoints] = useState<Point[]>([]);
  const [snapshotUrl, setSnapshotUrl] = useState<string | null>(null);

  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/cameras"),
  });
  const presetsQuery = useQuery({
    queryKey: ["rule-presets"],
    queryFn: () => api<RulePresetsResponse>("/api/rules/presets"),
  });

  const cameras = camerasQuery.data ?? [];
  const paramId = Number(searchParams.get("camera"));
  const cameraId = Number.isFinite(paramId) && paramId > 0 ? paramId : cameras[0]?.id;
  const selectedCamera = cameras.find((c) => c.id === cameraId);

  const rulesQuery = useQuery({
    queryKey: ["camera-rules", cameraId],
    queryFn: () => api<CameraRule[]>(`/cameras/${cameraId}/rules`),
    enabled: Number.isFinite(cameraId),
  });

  useEffect(() => {
    if (camerasQuery.isError) toast.error(errorMessage(camerasQuery.error));
  }, [camerasQuery.isError, camerasQuery.error]);

  function pickCamera(id: number) {
    setSearchParams({ camera: String(id) });
    setStep(1);
    setPreset(null);
    setPoints([]);
    setSnapshotUrl(null);
  }

  useEffect(() => {
    const list = camerasQuery.data;
    if (list?.length && !searchParams.get("camera")) {
      setSearchParams({ camera: String(list[0].id) }, { replace: true });
    }
  }, [camerasQuery.data, searchParams, setSearchParams]);

  const saveRule = useMutation({
    mutationFn: async () => {
      if (!preset || !Number.isFinite(cameraId)) throw new Error("请选择摄像头和场景");
      const name = String(values.name ?? "").trim() || preset.display_name;
      return api(`/cameras/${cameraId}/rules`, jsonBody("POST", {
        name,
        type: preset.type,
        params: buildRuleParams(preset, values, points),
        cooldown: Number(values.cooldown) || 30,
      }));
    },
    onSuccess: async () => {
      toast.success(`规则「${String(values.name || preset?.display_name)}」已保存`);
      setPoints([]);
      setPreset(null);
      setStep(1);
      setSnapshotUrl(null);
      await queryClient.invalidateQueries({ queryKey: ["camera-rules", cameraId] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const deleteRule = useMutation({
    mutationFn: (ruleId: number) =>
      api(`/cameras/${cameraId}/rules/${ruleId}`, { method: "DELETE" }),
    onSuccess: async () => {
      toast.success("规则已删除");
      await queryClient.invalidateQueries({ queryKey: ["camera-rules", cameraId] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  async function goStep3() {
    if (!preset) return;
    if (!preset.needs_zone) {
      saveRule.mutate();
      return;
    }
    if (!Number.isFinite(cameraId)) return;
    setPoints([]);
    setSnapshotUrl(`/cameras/${cameraId}/snapshot.jpg?t=${Date.now()}`);
    setStep(3);
  }

  const isLine = preset?.zone_shape === "line";
  const minPoints = isLine ? 2 : 3;
  const canSaveZone = isLine ? points.length === 2 : points.length >= 3;

  const columns = useMemo<DataTableColumn<CameraRule>[]>(
    () => [
      {
        accessorKey: "name",
        header: "规则",
        cell: ({ row }) => ruleDisplayName(row.original),
      },
      {
        accessorKey: "type",
        header: "类型",
        cell: ({ row }) => (
          <Badge variant="secondary">
            {RULE_TYPE_NAMES[row.original.type] || row.original.type}
          </Badge>
        ),
      },
      {
        id: "summary",
        header: "说明",
        cell: ({ row }) => (
          <span className="text-muted-foreground">{ruleParamSummary(row.original) || "—"}</span>
        ),
      },
      {
        accessorKey: "cooldown",
        header: "冷却",
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.cooldown}s</span>,
      },
      {
        accessorKey: "enabled",
        header: "启用",
        cell: ({ row }) => (row.original.enabled ? "是" : "否"),
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <Button
            size="xs"
            variant="destructive"
            onClick={() => deleteRule.mutate(row.original.id)}
          >
            删除
          </Button>
        ),
      },
    ],
    [deleteRule],
  );

  const commonClasses = presetsQuery.data?.common_classes ?? [];
  const showStep3 = Boolean(preset?.needs_zone);

  return (
    <div className="space-y-6">
      <PageHeader
        title="规则"
        description="选场景、填参数，需要区域的在画面上点选多边形或计数线。"
      />

      {camerasQuery.isPending ? (
        <p className="text-sm text-muted-foreground">正在加载摄像头…</p>
      ) : cameras.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          请先添加摄像头。到
          <Link className="mx-1 underline" to="/cameras">
            摄像头
          </Link>
          页新建一路。
        </p>
      ) : (
        <div className="grid max-w-md gap-1.5">
          <Label>摄像头</Label>
          <Select
            value={Number.isFinite(cameraId) ? String(cameraId) : undefined}
            onValueChange={(next) => {
              if (next) pickCamera(Number(next));
            }}
          >
            <SelectTrigger>
              <SelectValue>
                {selectedCamera ? `[${selectedCamera.id}] ${selectedCamera.name}` : "选择摄像头"}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              {cameras.map((cam) => (
                <SelectItem key={cam.id} value={String(cam.id)}>
                  [{cam.id}] {cam.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}

      {cameras.length > 0 ? (
        <>
          <div className="flex flex-wrap gap-2 text-sm">
            {([1, 2, 3] as const).map((n) => {
              if (n === 3 && !showStep3 && step !== 3) return null;
              return (
                <span
                  key={n}
                  className={cn(
                    "rounded-md border px-2 py-1",
                    step === n ? "border-foreground" : "text-muted-foreground",
                  )}
                >
                  {n === 1 ? "① 选场景" : n === 2 ? "② 填参数" : "③ 画区域"}
                </span>
              );
            })}
          </div>

          {step === 1 ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                这条规则想解决什么问题？选一个最接近的场景：
              </p>
              {presetsQuery.isError ? (
                <p className="text-sm text-destructive">{errorMessage(presetsQuery.error)}</p>
              ) : (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {(presetsQuery.data?.presets ?? []).map((item) => (
                    <button
                      key={item.type}
                      type="button"
                      className="rounded-lg border p-4 text-left hover:bg-muted/60"
                      onClick={() => {
                        setPreset(item);
                        setValues(defaultFieldValues(item));
                        setPoints([]);
                        setStep(2);
                      }}
                    >
                      <h2 className="text-base font-medium">{item.display_name}</h2>
                      <p className="mt-1 text-sm">{item.tagline}</p>
                      <p className="mt-2 text-sm text-muted-foreground">{item.description}</p>
                      <ul className="mt-2 list-disc space-y-0.5 pl-4 text-sm text-muted-foreground">
                        {item.scenarios.map((s) => (
                          <li key={s}>{s}</li>
                        ))}
                      </ul>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : null}

          {step === 2 && preset ? (
            <div className="max-w-xl space-y-4 rounded-lg border p-4">
              {preset.fields.map((field) => (
                <div key={field.key} className="grid gap-1.5">
                  <Label htmlFor={`rf-${field.key}`}>{field.label}</Label>
                  {field.kind === "class" ? (
                    <Select
                      value={fieldValue(values, field)}
                      onValueChange={(next) =>
                        setValues((prev) => ({ ...prev, [field.key]: next ? [next] : [] }))
                      }
                    >
                      <SelectTrigger id={`rf-${field.key}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {commonClasses.map((c) => (
                          <SelectItem key={c.id} value={c.id}>
                            {c.name}（{c.id}）
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : field.kind === "direction" ? (
                    <Select
                      value={fieldValue(values, field) || "both"}
                      onValueChange={(next) =>
                        setValues((prev) => ({ ...prev, [field.key]: next ?? "both" }))
                      }
                    >
                      <SelectTrigger id={`rf-${field.key}`}>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(DIRECTION_NAMES).map(([v, n]) => (
                          <SelectItem key={v} value={v}>
                            {n}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  ) : (
                    <div className="flex items-center gap-2">
                      <Input
                        id={`rf-${field.key}`}
                        type={field.kind === "number" ? "number" : "text"}
                        value={fieldValue(values, field)}
                        onChange={(e) => {
                          const next =
                            field.kind === "number" ? Number(e.target.value) || field.default : e.target.value;
                          setValues((prev) => ({ ...prev, [field.key]: next }));
                        }}
                      />
                      {field.unit ? (
                        <span className="text-sm text-muted-foreground">{field.unit}</span>
                      ) : null}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {field.hint}
                    {field.kind === "class" && presetsQuery.data?.classes_note
                      ? `；${presetsQuery.data.classes_note}`
                      : ""}
                    {field.kind === "direction"
                      ? "（沿线第一点→第二点看，左→右为进）"
                      : ""}
                  </p>
                </div>
              ))}
              <div className="flex flex-wrap gap-2">
                <Button onClick={() => void goStep3()}>
                  {preset.needs_zone ? "下一步" : "保存规则"}
                </Button>
                <Button variant="destructive" onClick={() => setStep(1)}>
                  返回重选
                </Button>
              </div>
            </div>
          ) : null}

          {step === 3 && preset && snapshotUrl ? (
            <div className="space-y-3 rounded-lg border p-4">
              <RuleCanvas
                snapshotUrl={snapshotUrl}
                existing={rulesQuery.data ?? []}
                points={points}
                zoneShape={isLine ? "line" : "polygon"}
                onPointsChange={setPoints}
                onReady={(ok) => {
                  if (!ok) {
                    toast.error("该摄像头暂无画面（未启动？），先启动摄像头再画区域");
                    setStep(2);
                    setSnapshotUrl(null);
                  }
                }}
                onHint={(message) => toast.success(message)}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  disabled={points.length < minPoints}
                  onClick={() =>
                    toast.success(
                      isLine ? "线段已完成" : `多边形已闭合（${points.length} 个顶点）`,
                    )
                  }
                >
                  {isLine ? "完成线段" : "闭合多边形"}
                </Button>
                <Button
                  variant="outline"
                  disabled={points.length === 0}
                  onClick={() => setPoints((prev) => prev.slice(0, -1))}
                >
                  撤销点
                </Button>
                <Button disabled={!canSaveZone} onClick={() => saveRule.mutate()}>
                  保存规则
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => {
                    setStep(2);
                    setSnapshotUrl(null);
                  }}
                >
                  返回改参数
                </Button>
              </div>
              <p className="text-sm text-muted-foreground">
                {isLine
                  ? "在画布上点击两个点画出计数线；再次点击可重画。方向约定：沿第一点→第二点看，左→右为进。"
                  : "在画布上点击添加顶点，至少 3 个点；双击画布也可闭合。"}
              </p>
            </div>
          ) : null}
        </>
      ) : null}

      <section className="space-y-2">
        <h2 className="text-lg font-medium">已有规则</h2>
        {!Number.isFinite(cameraId) ? (
          <p className="text-sm text-muted-foreground">请先添加摄像头</p>
        ) : (
          <DataTable
            columns={columns}
            data={rulesQuery.data ?? []}
            getRowId={(row) => String(row.id)}
            emptyMessage="该摄像头还没有规则，从上面的场景卡片开始吧。"
          />
        )}
      </section>
    </div>
  );
}
