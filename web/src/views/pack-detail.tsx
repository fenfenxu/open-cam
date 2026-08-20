import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import { jsonBody, type Camera, type VideoAsset } from "@/lib/cameras";
import {
  AVAILABILITY_NAMES,
  ORIGIN_NAMES,
  packAssetUrl,
  sceneHasMedia,
  type PackApplyPlan,
  type PackDeployment,
  type PackApplyResult,
  type PackDetail,
  type PackScene,
  type PackTrial,
} from "@/lib/packs";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

function fmtSec(sec: number): string {
  const total = Math.max(0, Math.round(sec));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const INTENT_NAMES: Record<string, string> = {
  observe: "观察记录",
  alert: "待办处置",
};

/** 系统「减少动态效果」开启时不自动播放/循环演示视频。 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(
    () =>
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

export function PackDetailPage() {
  const pathname = usePathname() || "";
  const packId = decodeURIComponent(pathname.split("/").filter(Boolean)[1] ?? "");

  const detailQuery = useQuery({
    queryKey: ["pack", packId],
    queryFn: () => api<PackDetail>(`/api/packs/${encodeURIComponent(packId)}`),
    enabled: Boolean(packId),
    retry: false,
  });

  if (!packId) {
    return <p className="text-sm text-muted-foreground">缺少方案包 id</p>;
  }
  if (detailQuery.isPending) {
    return <p className="text-sm text-muted-foreground">正在加载方案详情…</p>;
  }
  if (detailQuery.isError) {
    const err = detailQuery.error;
    const missing = err instanceof ApiError && err.status === 404;
    return (
      <div className="space-y-3">
        <p className="text-sm">
          <Link prefetch={false} className="underline" href="/marketplace">
            ← 返回方案市场
          </Link>
        </p>
        <p className="text-sm text-muted-foreground">
          {missing ? "方案包不存在或已卸载。" : `加载失败：${errorMessage(err)}`}
        </p>
      </div>
    );
  }
  return <PackDetailView detail={detailQuery.data} />;
}

function PackDetailView({ detail }: { detail: PackDetail }) {
  const queryClient = useQueryClient();
  const [selectedCam, setSelectedCam] = useState("");
  const [plan, setPlan] = useState<PackApplyPlan | null>(null);
  const [planOpen, setPlanOpen] = useState(false);
  const [deploymentId, setDeploymentId] = useState<number | null>(null);
  const available = detail.availability === "available";
  const scenes = detail.experience.scenes;

  useEffect(() => {
    if (typeof window === "undefined") return;
    const queryId = new URLSearchParams(window.location.search).get("deployment");
    const storedId = window.localStorage.getItem(`opencam:deployment:${detail.id}`);
    const id = Number(queryId || storedId);
    if (Number.isInteger(id) && id > 0) setDeploymentId(id);
  }, [detail.id]);

  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
    enabled: available && detail.application.mode === "existing_camera",
  });
  const cameras = camerasQuery.data ?? [];

  const applyPlan = useMutation({
    mutationFn: async () => {
      const body =
        detail.application.mode === "existing_camera"
          ? { camera_id: Number(selectedCam) }
          : {};
      return api<PackApplyPlan>(
        `/api/packs/${encodeURIComponent(detail.id)}/apply-plan`,
        jsonBody("POST", body),
      );
    },
    onSuccess: (data) => {
      setPlan(data);
      setPlanOpen(true);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const apply = useMutation({
    mutationFn: async () => {
      if (!plan) throw new Error("请先生成应用计划");
      const body = {
        ...(detail.application.mode === "existing_camera"
          ? { camera_id: Number(selectedCam) }
          : {}),
        expected_fingerprint: plan.fingerprint,
      };
      return api<PackApplyResult>(
        `/api/packs/${encodeURIComponent(detail.id)}/apply`,
        jsonBody("POST", body),
      );
    },
    onSuccess: async (data) => {
      const nextDeploymentId = data.deployment_id ?? null;
      setPlanOpen(false);
      setPlan(null);
      if (nextDeploymentId) {
        setDeploymentId(nextDeploymentId);
        window.localStorage.setItem(
          `opencam:deployment:${detail.id}`,
          String(nextDeploymentId),
        );
      }
      if (detail.application.mode === "existing_camera") {
        toast.success(`已应用 ${data.rules?.length ?? 0} 条规则，请按清单完成校准`);
      } else {
        toast.success(
          `已创建 ${data.cameras?.length ?? 0} 路停止态摄像头，请到「摄像头」页换成真实源后校准启用`,
        );
      }
      await queryClient.invalidateQueries({ queryKey: ["cameras"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const rulesById = useMemo(
    () => new Map(detail.rules.map((r) => [r.id, r])),
    [detail.rules],
  );

  // 旧包必须由用户明确选择目标摄像头后才可应用，不默认第一台
  const canApply =
    available &&
    (detail.application.mode !== "existing_camera" ||
      (cameras.length > 0 && selectedCam !== ""));

  return (
    <div className="space-y-8">
      <p className="text-sm">
        <Link prefetch={false} className="underline" href="/marketplace">
          ← 返回方案市场
        </Link>
      </p>

      {/* Hero 概览 */}
      <header className="space-y-3">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{detail.vertical}</span>
          <Badge variant="secondary">{ORIGIN_NAMES[detail.origin] ?? detail.origin}</Badge>
          <span>v{detail.version}</span>
          {detail.author ? <span>作者：{detail.author}</span> : null}
          {detail.availability !== "available" ? (
            <Badge variant="destructive">
              {AVAILABILITY_NAMES[detail.availability] ?? detail.availability}
            </Badge>
          ) : null}
        </div>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h1 className="text-2xl font-medium">{detail.name}</h1>
            <p className="text-sm text-muted-foreground">
              {detail.presentation.tagline || detail.description}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {scenes.length ? (
              <Button variant="outline" render={<a href="#pack-experience" />}>
                体验方案
              </Button>
            ) : (
              <Button variant="outline" disabled>
                体验方案
              </Button>
            )}
            <Button
              disabled={!canApply || applyPlan.isPending || apply.isPending}
              onClick={() => applyPlan.mutate()}
            >
              应用方案
            </Button>
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          {detail.application.camera_count} 路机位 · {detail.application.rule_count} 条规则 ·{" "}
          {scenes.length} 个场景 · 本机运行 · 数据不出本机
        </p>
        {!available ? (
          <p role="alert" className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm">
            {detail.unavailable_reason || "方案包当前不可用"}
            {detail.availability === "incompatible"
              ? "；升级 open-cam 后再试。详情与效果演示仍可查看。"
              : "。详情仍可查看，但不能体验或应用。"}
          </p>
        ) : null}
      </header>

      {/* 效果体验工作台 */}
      {scenes.length ? <ExperienceWorkbench detail={detail} /> : null}

      {deploymentId ? (
        <DeploymentChecklist
          deploymentId={deploymentId}
          onClear={() => {
            setDeploymentId(null);
            window.localStorage.removeItem(`opencam:deployment:${detail.id}`);
          }}
        />
      ) : null}

      {/* 能解决什么 */}
      {detail.presentation.outcomes.length ? (
        <section className="space-y-3">
          <h2 className="text-lg font-medium">能解决什么</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {detail.presentation.outcomes.map((o) => (
              <div key={o.title} className="space-y-1 rounded-lg border p-3">
                <h3 className="text-sm font-medium">{o.title}</h3>
                <p className="text-sm text-muted-foreground">{o.description}</p>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {/* 摄像头怎么装 */}
      <section className="space-y-3">
        <h2 className="text-lg font-medium">摄像头怎么装</h2>
        {detail.application.mode === "existing_camera" ? (
          <p className="text-sm text-muted-foreground">
            该方案应用到一台已有摄像头：全部 {detail.application.rule_count}{" "}
            条规则会挂到所选摄像头，应用前需要先选择目标摄像头。
          </p>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-2">
          {detail.cameras.map((cam) => {
            const rules = cam.rule_ids
              .map((id) => rulesById.get(id))
              .filter((r) => r != null);
            return (
              <article key={cam.id} className="space-y-2 rounded-lg border p-3">
                {cam.poster_asset_id ? (
                  <img
                    src={packAssetUrl(detail.id, cam.poster_asset_id)}
                    alt={`${cam.name}效果海报`}
                    className="aspect-video w-full rounded-md object-cover"
                    loading="lazy"
                  />
                ) : null}
                <h3 className="text-sm font-medium">{cam.name}</h3>
                {cam.purpose ? (
                  <p className="text-sm text-muted-foreground">{cam.purpose}</p>
                ) : null}
                {cam.placement ? (
                  <p className="text-xs text-muted-foreground">安装建议：{cam.placement}</p>
                ) : null}
                {rules.length ? (
                  <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                    {rules.map((r) => (
                      <li key={r.id}>
                        <span className="text-foreground">{r.name}</span>
                        {`（${r.type_label}）`}：{r.summary}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </article>
            );
          })}
        </div>
      </section>

      {/* 运行要求与应用影响 */}
      <section className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3 rounded-lg border p-4">
          <h2 className="text-lg font-medium">运行要求与限制</h2>
          {detail.presentation.requirements.length ? (
            <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
              {detail.presentation.requirements.map((req) => (
                <li key={req}>{req}</li>
              ))}
            </ul>
          ) : null}
          {detail.presentation.limitations.length ? (
            <>
              <h3 className="text-sm font-medium">适用限制</h3>
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {detail.presentation.limitations.map((lim) => (
                  <li key={lim}>{lim}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
        <div className="space-y-3 rounded-lg border p-4">
          <h2 className="text-lg font-medium">应用后会发生什么</h2>
          <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {detail.application.mode === "create_cameras" ? (
              <>
                <li>
                  新建 {detail.application.camera_count} 路停止态摄像头：
                  {detail.cameras.map((c) => c.name).join("、")}
                </li>
                <li>新建 {detail.application.rule_count} 条规则并挂到对应摄像头</li>
                <li>不自动启动推理；换成真实源、校准区域后逐路启用</li>
                <li>不覆盖已有摄像头和规则；重复应用会创建另一套</li>
              </>
            ) : (
              <>
                <li>
                  全部 {detail.application.rule_count} 条规则挂到所选的一台已有摄像头
                </li>
                <li>不新建摄像头，不改变摄像头运行状态</li>
              </>
            )}
            {detail.application.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
          {available ? (
            <div className="space-y-2 pt-2">
              {detail.application.mode === "existing_camera" ? (
                cameras.length ? (
                  <Select
                    value={selectedCam}
                    onValueChange={(v) => v && setSelectedCam(String(v))}
                  >
                    <SelectTrigger className="w-full" aria-label="选择要应用的摄像头">
                      <SelectValue>
                        {(() => {
                          const camera = cameras.find(
                            (item) => String(item.id) === selectedCam,
                          );
                          return camera
                            ? `应用到：[${camera.id}] ${camera.name}`
                            : "选择要应用的摄像头";
                        })()}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {cameras.map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}>
                          [{c.id}] {c.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <p className="text-sm text-muted-foreground">
                    还没有摄像头，请先到
                    <Link prefetch={false} className="underline" href="/cameras">
                      「摄像头」页
                    </Link>
                    创建后再应用。
                  </p>
                )
              ) : null}
              <Button
                disabled={!canApply || applyPlan.isPending || apply.isPending}
                onClick={() => applyPlan.mutate()}
              >
                应用方案
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              方案包当前不可用，不能应用。
            </p>
          )}
        </div>
      </section>

      {/* 说明文档与版本信息 */}
      <section className="space-y-3">
        <h2 className="text-lg font-medium">详细说明</h2>
        {detail.readme_html ? (
          // 服务端已按允许列表清洗（去脚本/危险 URL），可直接渲染
          <div
            className="max-w-none text-sm [&_a]:underline"
            dangerouslySetInnerHTML={{ __html: detail.readme_html }}
          />
        ) : (
          <p className="text-sm text-muted-foreground">{detail.description}</p>
        )}
        <p className="text-xs text-muted-foreground">
          版本 v{detail.version} · 要求 open-cam ≥ {detail.min_opencam_version} · 作者：
          {detail.author || "匿名"} · 内容指纹 {detail.fingerprint.slice(0, 12)}
        </p>
      </section>

      <ApplyPlanDialog
        open={planOpen}
        plan={plan}
        confirming={apply.isPending}
        onOpenChange={setPlanOpen}
        onConfirm={() => apply.mutate()}
      />
    </div>
  );
}

function ApplyPlanDialog({
  open,
  plan,
  confirming,
  onOpenChange,
  onConfirm,
}: {
  open: boolean;
  plan: PackApplyPlan | null;
  confirming: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>确认应用方案</DialogTitle>
          <DialogDescription>
            服务端已根据当前方案内容计算变更。确认时会再次校验指纹，内容变化会要求重新预览。
          </DialogDescription>
        </DialogHeader>
        {plan ? (
          <div className="space-y-4">
            <div className="grid gap-2 sm:grid-cols-3">
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">摄像头</p>
                <p className="font-medium">{plan.cameras.length} 路</p>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">规则</p>
                <p className="font-medium">{plan.rules.length} 条</p>
              </div>
              <div className="rounded-md border p-3">
                <p className="text-xs text-muted-foreground">内容指纹</p>
                <p className="font-mono text-xs">{plan.fingerprint.slice(0, 16)}</p>
              </div>
            </div>
            <div className="space-y-2">
              <h3 className="text-sm font-medium">将要变更</h3>
              <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                {plan.cameras.map((camera) => (
                  <li key={camera.slot_id}>
                    {camera.action === "create" ? "新建" : "绑定"}摄像头「{camera.name}」
                    {camera.source_hint ? `（${camera.source_hint}）` : ""}
                  </li>
                ))}
                {plan.videos.map((video) => (
                  <li key={`${video.camera_slot_id}-${video.filename}`}>
                    复制演示源「{video.filename}」
                  </li>
                ))}
                {plan.rules.map((rule) => (
                  <li key={`${rule.camera_slot_id}-${rule.name}`}>
                    新建规则「{rule.name}」（{rule.type}）
                  </li>
                ))}
              </ul>
            </div>
            {plan.will_not.length ? (
              <div className="space-y-2 rounded-md bg-muted/50 p-3">
                <h3 className="text-sm font-medium">不会发生</h3>
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                  {plan.will_not.map((item) => <li key={item}>{item}</li>)}
                </ul>
              </div>
            ) : null}
            {plan.next_steps.length ? (
              <div className="space-y-2">
                <h3 className="text-sm font-medium">应用后继续</h3>
                <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
                  {plan.next_steps.map((item) => <li key={item}>{item}</li>)}
                </ol>
              </div>
            ) : null}
            {plan.warnings.map((warning) => (
              <p key={warning} className="text-sm text-amber-700 dark:text-amber-300">
                {warning}
              </p>
            ))}
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={confirming}>
            返回修改
          </Button>
          <Button onClick={onConfirm} disabled={!plan || confirming}>
            {confirming ? "正在应用…" : "确认并创建部署"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 效果体验工作台：场景切换 + 原始/结果媒体切换 + 事件时间线跳转。 */
function ExperienceWorkbench({ detail }: { detail: PackDetail }) {
  const scenes = detail.experience.scenes;
  const [sceneId, setSceneId] = useState(scenes[0]?.id ?? "");
  const scene = scenes.find((s) => s.id === sceneId) ?? scenes[0];

  return (
    <section id="pack-experience" className="space-y-3 scroll-mt-4">
      <h2 className="text-lg font-medium">效果体验</h2>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="体验场景">
        {scenes.map((s) => (
          <Button
            key={s.id}
            size="sm"
            variant={s.id === scene.id ? "default" : "outline"}
            role="tab"
            aria-selected={s.id === scene.id}
            onClick={() => setSceneId(s.id)}
          >
            {s.title}
          </Button>
        ))}
      </div>
      <ScenePlayer key={scene.id} detail={detail} scene={scene} />
      <TrialWorkbench detail={detail} scene={scene} />
      <p className="text-xs text-muted-foreground">
        这是方案自带的效果演示，用于说明检测逻辑；真实效果取决于机位、画质、模型和规则校准。
      </p>
    </section>
  );
}

function TrialWorkbench({ detail, scene }: { detail: PackDetail; scene: PackScene }) {
  const [trialId, setTrialId] = useState<string | null>(null);
  const [sourceKind, setSourceKind] = useState<"pack" | "video" | "camera">("pack");
  const [videoId, setVideoId] = useState("");
  const [cameraId, setCameraId] = useState("");
  const trialCameras = useQuery({
    queryKey: ["cameras", "pack-trial"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });
  const trialVideos = useQuery({
    queryKey: ["videos", "pack-trial"],
    queryFn: () => api<VideoAsset[]>("/api/videos"),
  });
  const trialQuery = useQuery({
    queryKey: ["pack-trial", trialId],
    queryFn: () => api<PackTrial>(`/api/pack-trials/${encodeURIComponent(trialId ?? "")}`),
    enabled: Boolean(trialId),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === "running" ? 1000 : false,
  });
  const startTrial = useMutation({
    mutationFn: () => {
      const source = {
        kind: sourceKind,
        ...(sourceKind === "video" ? { video_id: Number(videoId) } : {}),
        ...(sourceKind === "camera" ? { camera_id: Number(cameraId) } : {}),
      };
      return api<PackTrial>(
        `/api/packs/${encodeURIComponent(detail.id)}/trials`,
        jsonBody("POST", { scene_id: scene.id, source }),
      );
    },
    onSuccess: (data) => setTrialId(data.id),
    onError: (err) => toast.error(errorMessage(err)),
  });
  const stopTrial = useMutation({
    mutationFn: () => api(`/api/pack-trials/${encodeURIComponent(trialId ?? "")}`, { method: "DELETE" }),
    onSuccess: () => {
      setTrialId(null);
      toast.success("试跑已停止，临时资源已释放");
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const trial = trialQuery.data;
  const running = trial?.status === "running";
  const canTrial = detail.availability === "available" && scene.available && scene.trial_available;
  const canStart = canTrial && !trialId && !startTrial.isPending &&
    (sourceKind === "pack" ||
      (sourceKind === "video" && Boolean(videoId)) ||
      (sourceKind === "camera" && Boolean(cameraId)));

  return (
    <div className="space-y-3 rounded-lg border bg-muted/20 p-4" aria-label="本机隔离试跑">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium">本机隔离试跑</h3>
          <p className="text-xs text-muted-foreground">
            只处理当前场景，默认最多 60 秒；不写事件、快照，不调用 VLM 或通知。
          </p>
        </div>
        <Badge variant={canTrial ? "outline" : "secondary"}>
          {canTrial ? "可试跑" : "当前场景不可试跑"}
        </Badge>
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="grid gap-1 text-xs">
          <span className="text-muted-foreground">输入来源</span>
          <select
            aria-label="试跑来源"
            className="h-9 min-w-48 rounded-md border bg-background px-2 text-sm"
            value={sourceKind}
            disabled={Boolean(trialId) || startTrial.isPending}
            onChange={(event) => setSourceKind(event.target.value as typeof sourceKind)}
          >
            <option value="pack">方案包试跑源</option>
            <option value="video">视频库</option>
            <option value="camera">正在运行的摄像头</option>
          </select>
        </label>
        {sourceKind === "video" ? (
          <label className="grid gap-1 text-xs">
            <span className="text-muted-foreground">视频</span>
            <select
              aria-label="选择试跑视频"
              className="h-9 min-w-48 rounded-md border bg-background px-2 text-sm"
              value={videoId}
              disabled={Boolean(trialId)}
              onChange={(event) => setVideoId(event.target.value)}
            >
              <option value="">请选择视频</option>
              {(trialVideos.data ?? []).map((video) => (
                <option key={video.id} value={video.id}>{video.filename}</option>
              ))}
            </select>
          </label>
        ) : null}
        {sourceKind === "camera" ? (
          <label className="grid gap-1 text-xs">
            <span className="text-muted-foreground">摄像头</span>
            <select
              aria-label="选择试跑摄像头"
              className="h-9 min-w-48 rounded-md border bg-background px-2 text-sm"
              value={cameraId}
              disabled={Boolean(trialId)}
              onChange={(event) => setCameraId(event.target.value)}
            >
              <option value="">请选择运行中的摄像头</option>
              {(trialCameras.data ?? []).filter((camera) => camera.status === "running").map((camera) => (
                <option key={camera.id} value={camera.id}>{camera.name}</option>
              ))}
            </select>
          </label>
        ) : null}
        {running ? (
          <Button variant="destructive" onClick={() => stopTrial.mutate()} disabled={stopTrial.isPending}>
            {stopTrial.isPending ? "正在停止…" : "停止试跑"}
          </Button>
        ) : (
          <Button onClick={() => startTrial.mutate()} disabled={!canStart}>
            {startTrial.isPending ? "正在启动…" : "开始试跑"}
          </Button>
        )}
      </div>
      {trialQuery.isError ? (
        <p role="alert" className="text-sm text-destructive">试跑状态读取失败：{errorMessage(trialQuery.error)}</p>
      ) : null}
      {trial?.status === "error" ? (
        <p role="alert" className="text-sm text-destructive">试跑失败：{trial.error || "体验源或检测器不可用"}</p>
      ) : null}
      {trial ? (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(16rem,0.7fr)]">
          <div className="space-y-2">
            {running ? (
              <img
                src={trial.live_url}
                alt={`${scene.title}本机试跑画面`}
                className="aspect-video w-full rounded-md bg-black object-contain"
              />
            ) : (
              <div className="flex aspect-video items-center justify-center rounded-md border bg-muted/40 text-sm text-muted-foreground">
                试跑{trial.status === "expired" ? "已过期" : "已结束"}，可重新开始。
              </div>
            )}
            <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span>状态：{running ? "运行中" : trial.status}</span>
              <span>处理：{trial.fps.toFixed(1)} FPS</span>
              <span>设备：{trial.device || "本机"}</span>
              <span>剩余：{Math.ceil(trial.remaining_sec)} 秒</span>
              <span>画面：{trial.width}×{trial.height}</span>
            </div>
          </div>
          <div className="space-y-3">
            <div>
              <h4 className="text-sm font-medium">规则状态</h4>
              <ul className="mt-2 space-y-1 text-sm">
                {trial.rules.map((rule) => (
                  <li key={rule.id} className="flex items-center justify-between gap-2 rounded-md border px-2 py-1.5">
                    <span>{rule.name}</span>
                    <span className={rule.matched ? "text-green-700 dark:text-green-300" : "text-muted-foreground"}>
                      {rule.matched ? `命中 ${rule.hits} 次` : `未命中 · ${rule.hits} 次`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="text-sm font-medium">临时命中时间线</h4>
              {trial.hits.length ? (
                <ul className="mt-2 max-h-36 space-y-1 overflow-auto text-xs">
                  {trial.hits.map((hit, index) => (
                    <li key={`${hit.rule_id}-${hit.at_sec}-${index}`} className="flex gap-2 rounded-md border px-2 py-1.5">
                      <span className="font-mono text-muted-foreground">{fmtSec(hit.at_sec)}</span>
                      <span>{hit.rule_name} · 置信度 {(hit.confidence * 100).toFixed(0)}%</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mt-2 text-xs text-muted-foreground">暂未命中，继续观察画面。</p>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function ScenePlayer({ detail, scene }: { detail: PackDetail; scene: PackScene }) {
  const reducedMotion = usePrefersReducedMotion();
  const videoRef = useRef<HTMLVideoElement>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const [mediaKind, setMediaKind] = useState<"result" | "input">(
    scene.result_asset_id ? "result" : "input",
  );
  const [mediaError, setMediaError] = useState(false);

  const hasMedia = sceneHasMedia(scene);
  const assetId =
    mediaKind === "result"
      ? (scene.result_asset_id ?? scene.input_asset_id)
      : (scene.input_asset_id ?? scene.result_asset_id);
  const mediaUrl = assetId ? packAssetUrl(detail.id, assetId) : null;
  const posterUrl = scene.poster_asset_id
    ? packAssetUrl(detail.id, scene.poster_asset_id)
    : undefined;
  const canToggle = Boolean(scene.input_asset_id && scene.result_asset_id);

  function switchMedia(kind: "result" | "input") {
    if (kind === mediaKind) return;
    pendingSeekRef.current = videoRef.current?.currentTime ?? null;
    setMediaError(false);
    setMediaKind(kind);
  }

  function seekTo(sec: number) {
    const v = videoRef.current;
    if (!v) return;
    v.currentTime = sec;
    void v.play().catch(() => undefined);
  }

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div className="space-y-2">
        {hasMedia && !mediaError && mediaUrl ? (
          <video
            ref={videoRef}
            key={mediaUrl}
            src={mediaUrl}
            poster={posterUrl}
            controls
            playsInline
            muted
            autoPlay={!reducedMotion}
            loop={!reducedMotion}
            className="aspect-video w-full rounded-md bg-black"
            onLoadedMetadata={() => {
              if (pendingSeekRef.current != null && videoRef.current) {
                videoRef.current.currentTime = pendingSeekRef.current;
                pendingSeekRef.current = null;
              }
            }}
            onError={() => setMediaError(true)}
          />
        ) : (
          <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-md border bg-muted/40 p-4 text-center">
            {posterUrl ? (
              <img
                src={posterUrl}
                alt={`${scene.title}海报`}
                className="max-h-full rounded-md object-contain"
              />
            ) : null}
            <p className="text-sm text-muted-foreground">
              {mediaError
                ? "当前浏览器无法播放该编码的演示视频。"
                : scene.degrade_reason || "该场景暂未提供视频演示。"}
            </p>
            {mediaError && mediaUrl ? (
              <a className="text-sm underline" href={mediaUrl} download>
                下载演示视频
              </a>
            ) : null}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          {canToggle ? (
            <div className="flex overflow-hidden rounded-md border" role="group" aria-label="画面切换">
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${mediaKind === "input" ? "bg-primary text-primary-foreground" : ""}`}
                aria-pressed={mediaKind === "input"}
                onClick={() => switchMedia("input")}
              >
                原始画面
              </button>
              <button
                type="button"
                className={`px-3 py-1.5 text-xs ${mediaKind === "result" ? "bg-primary text-primary-foreground" : ""}`}
                aria-pressed={mediaKind === "result"}
                onClick={() => switchMedia("result")}
              >
                检测效果
              </button>
            </div>
          ) : null}
          {hasMedia && !mediaError ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                if (videoRef.current) {
                  videoRef.current.currentTime = 0;
                  void videoRef.current.play().catch(() => undefined);
                }
              }}
            >
              重播演示
            </Button>
          ) : null}
        </div>
      </div>
      <div className="space-y-2">
        <h3 className="text-sm font-medium">{scene.title}</h3>
        {scene.degrade_reason && hasMedia ? (
          <p className="text-xs text-muted-foreground">{scene.degrade_reason}</p>
        ) : null}
        {scene.events.length ? (
          <ul className="space-y-1">
            {scene.events.map((ev, i) => (
              <li key={`${ev.at_sec}-${i}`}>
                <button
                  type="button"
                  className="flex w-full items-baseline gap-2 rounded-md border px-3 py-2 text-left text-sm hover:bg-accent"
                  onClick={() => seekTo(ev.at_sec)}
                >
                  <span className="shrink-0 font-mono text-xs text-muted-foreground">
                    {fmtSec(ev.at_sec)}
                  </span>
                  <span className="flex-1">
                    {ev.title}
                    {ev.result ? (
                      <span className="block text-xs text-muted-foreground">{ev.result}</span>
                    ) : null}
                  </span>
                  <Badge variant="outline" className="shrink-0">
                    {INTENT_NAMES[ev.intent] ?? ev.intent}
                  </Badge>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">该场景暂无事件样例。</p>
        )}
      </div>
    </div>
  );
}

const DEPLOYMENT_STATUS_NAMES: Record<string, string> = {
  configuring: "配置中",
  active: "已激活",
  degraded: "资源缺失，已降级",
};

function DeploymentChecklist({
  deploymentId,
  onClear,
}: {
  deploymentId: number;
  onClear: () => void;
}) {
  const deploymentQuery = useQuery({
    queryKey: ["pack-deployment", deploymentId],
    queryFn: () => api<PackDeployment>(`/api/pack-deployments/${deploymentId}`),
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.status === "configuring" ? 2000 : false,
  });
  const [localSteps, setLocalSteps] = useState<Record<string, boolean>>({});
  const storageKey = `opencam:deployment-steps:${deploymentId}`;

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      const saved = window.localStorage.getItem(storageKey);
      setLocalSteps(saved ? (JSON.parse(saved) as Record<string, boolean>) : {});
    } catch {
      setLocalSteps({});
    }
  }, [storageKey]);

  const configureRules = useMutation({
    mutationFn: async ({ resourceIds, configured }: { resourceIds: number[]; configured: boolean }) => {
      let result: PackDeployment | null = null;
      for (const resourceId of resourceIds) {
        result = await api<PackDeployment>(
          `/api/pack-deployments/${deploymentId}/resources/${resourceId}`,
          jsonBody("PATCH", { configured }),
        );
      }
      return result;
    },
    onSuccess: async () => {
      await deploymentQuery.refetch();
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const markLocal = (key: string, checked: boolean) => {
    const next = { ...localSteps, [key]: checked };
    setLocalSteps(next);
    window.localStorage.setItem(storageKey, JSON.stringify(next));
  };

  if (deploymentQuery.isPending) {
    return (
      <section className="space-y-2 rounded-lg border p-4">
        <h2 className="text-lg font-medium">部署激活清单</h2>
        <p className="text-sm text-muted-foreground">正在读取部署状态…</p>
      </section>
    );
  }
  if (deploymentQuery.isError || !deploymentQuery.data) {
    return (
      <section className="space-y-3 rounded-lg border border-destructive/40 p-4">
        <h2 className="text-lg font-medium">部署激活清单</h2>
        <p role="alert" className="text-sm text-destructive">
          无法读取部署 #{deploymentId}：{errorMessage(deploymentQuery.error)}
        </p>
        <Button variant="outline" onClick={onClear}>移除失效部署入口</Button>
      </section>
    );
  }

  const deployment = deploymentQuery.data;
  const slots = [...new Set(deployment.resources.map((resource) => resource.camera_slot_id))];
  const statusName = DEPLOYMENT_STATUS_NAMES[deployment.status] ?? deployment.status;
  const shareUrl = typeof window === "undefined"
    ? ""
    : `${window.location.origin}${window.location.pathname}?deployment=${deployment.id}`;

  return (
    <section className="space-y-4 rounded-lg border p-4" aria-label="部署激活清单">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-medium">部署激活清单</h2>
          <p className="text-xs text-muted-foreground">
            部署 #{deployment.id} · {deployment.pack_version} · 可复制链接，之后继续完成配置。
          </p>
        </div>
        <Badge variant={deployment.status === "active" ? "default" : deployment.status === "degraded" ? "destructive" : "secondary"}>
          {statusName}
        </Badge>
      </div>
      {deployment.status === "degraded" ? (
        <p role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          有摄像头、规则或视频资源已缺失，请先恢复资源；缺失资源不会被标记为完成。
        </p>
      ) : null}
      {shareUrl ? (
        <div className="flex flex-wrap items-center gap-2 rounded-md bg-muted/50 p-2">
          <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">{shareUrl}</span>
          <Button
            size="xs"
            variant="outline"
            onClick={() => {
              void navigator.clipboard?.writeText(shareUrl);
              toast.success("部署继续链接已复制");
            }}
          >
            复制继续链接
          </Button>
        </div>
      ) : null}
      <div className="space-y-3">
        {slots.map((slot) => {
          const resources = deployment.resources.filter((resource) => resource.camera_slot_id === slot);
          const camera = resources.find((resource) => resource.kind === "camera");
          const rules = resources.filter((resource) => resource.kind === "rule");
          const missing = resources.some((resource) => resource.missing);
          const calibrated = rules.length > 0 && rules.every((resource) => resource.configured);
          const step = (key: string) => `${slot}:${key}`;
          const local = (key: string) => Boolean(localSteps[step(key)]);
          const steps = [
            { key: "source", label: "换源", description: "换成真实 RTSP 或视频文件源" },
            { key: "view", label: "看画面", description: "打开详情确认视角、光线和画质" },
            { key: "start", label: "启动", description: "确认规则后启动这一路摄像头" },
            { key: "verify", label: "验证", description: "看到预期事件或统计后完成验收" },
          ];
          return (
            <article key={slot} className="space-y-3 rounded-md border p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <h3 className="text-sm font-medium">机位 {slot}</h3>
                  <p className="text-xs text-muted-foreground">{camera?.label || "摄像头资源缺失"}</p>
                </div>
                {camera && !camera.missing ? (
                  <Button size="xs" variant="outline" render={<Link prefetch={false} href={`/cameras/${camera.resource_id}`} />}>
                    打开摄像头详情
                  </Button>
                ) : null}
              </div>
              {missing ? <p className="text-xs text-destructive">该机位有资源缺失。</p> : null}
              <ol className="grid gap-2 sm:grid-cols-2">
                {steps.slice(0, 2).map((item) => (
                  <li key={item.key} className="flex items-start gap-2 rounded-md bg-muted/40 p-2">
                    <Checkbox
                      checked={local(item.key)}
                      disabled={!camera || camera.missing}
                      aria-label={`${slot} ${item.label}`}
                      onCheckedChange={(value) => markLocal(step(item.key), value === true)}
                    />
                    <span className="text-sm"><strong className="font-medium">{item.label}</strong><span className="block text-xs text-muted-foreground">{item.description}</span></span>
                  </li>
                ))}
                <li className="flex items-start gap-2 rounded-md bg-muted/40 p-2 sm:col-span-2">
                  <Checkbox
                    checked={calibrated}
                    disabled={missing || rules.length === 0 || configureRules.isPending}
                    aria-label={`${slot} 校准并启用规则`}
                    onCheckedChange={(value) => configureRules.mutate({
                      resourceIds: rules.map((resource) => resource.id),
                      configured: value === true,
                    })}
                  />
                  <span className="text-sm"><strong className="font-medium">校准并启用</strong><span className="block text-xs text-muted-foreground">校准区域、计数线和阈值后，逐条启用规则</span></span>
                </li>
                {steps.slice(2).map((item) => (
                  <li key={item.key} className="flex items-start gap-2 rounded-md bg-muted/40 p-2">
                    <Checkbox
                      checked={local(item.key)}
                      disabled={!camera || camera.missing || !calibrated}
                      aria-label={`${slot} ${item.label}`}
                      onCheckedChange={(value) => markLocal(step(item.key), value === true)}
                    />
                    <span className="text-sm"><strong className="font-medium">{item.label}</strong><span className="block text-xs text-muted-foreground">{item.description}</span></span>
                  </li>
                ))}
              </ol>
            </article>
          );
        })}
      </div>
    </section>
  );
}
