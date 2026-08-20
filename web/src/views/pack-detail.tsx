import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import { jsonBody, type Camera } from "@/lib/cameras";
import {
  AVAILABILITY_NAMES,
  ORIGIN_NAMES,
  packAssetUrl,
  sceneHasMedia,
  type PackApplyResult,
  type PackDetail,
  type PackScene,
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
  const available = detail.availability === "available";
  const scenes = detail.experience.scenes;

  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
    enabled: available && detail.application.mode === "existing_camera",
  });
  const cameras = camerasQuery.data ?? [];

  const apply = useMutation({
    mutationFn: async () => {
      const body =
        detail.application.mode === "existing_camera"
          ? { camera_id: Number(selectedCam) }
          : {};
      return api<PackApplyResult>(
        `/api/packs/${encodeURIComponent(detail.id)}/apply`,
        jsonBody("POST", body),
      );
    },
    onSuccess: async (data) => {
      if (detail.application.mode === "existing_camera") {
        toast.success(`已应用 ${data.rules?.length ?? 0} 条规则，可到「规则」页调整`);
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
              disabled={!canApply || apply.isPending}
              onClick={() => apply.mutate()}
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
                disabled={!canApply || apply.isPending}
                onClick={() => apply.mutate()}
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
    </div>
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
      <p className="text-xs text-muted-foreground">
        这是方案自带的效果演示，用于说明检测逻辑；真实效果取决于机位、画质、模型和规则校准。
      </p>
    </section>
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
