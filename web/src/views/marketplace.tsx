import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/app/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { jsonBody } from "@/lib/cameras";
import {
  AVAILABILITY_NAMES,
  ORIGIN_NAMES,
  packDetailPath,
  type PackCard,
} from "@/lib/packs";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

function experienceBadge(pack: PackCard): string {
  if (pack.availability !== "available") return "不可体验";
  if (pack.has_demo) return pack.trial_available ? "效果演示 · 可试跑" : "效果演示";
  return "演示降级";
}

export function MarketplacePage() {
  const queryClient = useQueryClient();
  const [source, setSource] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const packsQuery = useQuery({
    queryKey: ["packs", "cards"],
    queryFn: () => api<PackCard[]>("/api/packs?view=cards"),
  });
  const onlineQuery = useQuery({
    queryKey: ["packs-online"],
    queryFn: () => api<{ note?: string }>("/api/packs/online"),
  });

  const packs = packsQuery.data ?? [];

  useEffect(() => {
    if (packsQuery.isError) toast.error(errorMessage(packsQuery.error));
  }, [packsQuery.isError, packsQuery.error]);

  const install = useMutation({
    mutationFn: ({ source: installSource, file }: { source?: string; file?: File }) => {
      if (file) {
        const body = new FormData();
        body.append("file", file);
        return api<PackCard>("/api/packs/install-upload", { method: "POST", body });
      }
      return api<PackCard>(
        "/api/packs/install",
        jsonBody("POST", { source: installSource?.trim() ?? "" }),
      );
    },
    onSuccess: async (pack) => {
      toast.success(`已安装：${pack.name}`);
      setSource("");
      setSelectedFile(null);
      await queryClient.invalidateQueries({ queryKey: ["packs"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const uninstall = useMutation({
    mutationFn: (packId: string) => api(`/api/packs/${packId}`, { method: "DELETE" }),
    onSuccess: async () => {
      toast.success("已卸载");
      await queryClient.invalidateQueries({ queryKey: ["packs"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  return (
    <div className="space-y-6">
      <PageHeader
        title="方案市场"
        description="浏览内置/已安装方案包，进入详情了解业务价值、机位部署与检测效果。"
      />

      <form
        className="flex flex-wrap items-end gap-3 rounded-lg border p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!selectedFile && !source.trim()) {
            toast.error("请填写安装源");
            return;
          }
          install.mutate(selectedFile ? { file: selectedFile } : { source });
        }}
      >
        <div className="grid min-w-64 flex-1 gap-1.5">
          <Label htmlFor="install-src">安装源</Label>
          <Input
            id="install-src"
            value={source}
            onChange={(e) => {
              setSource(e.target.value);
              if (selectedFile) setSelectedFile(null);
            }}
            placeholder="本地目录 / pack.zip 路径 / https://... 包地址"
          />
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip,application/zip"
            aria-label="选择方案包文件"
            className="hidden"
            onChange={(e) => {
              setSelectedFile(e.target.files?.[0] ?? null);
              e.target.value = "";
            }}
          />
          <Button
            type="button"
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
            disabled={install.isPending}
          >
            选择方案包文件
          </Button>
          <Button type="submit" disabled={install.isPending}>
            安装
          </Button>
        </div>
        {selectedFile ? (
          <p className="basis-full text-xs text-muted-foreground">已选择：{selectedFile.name}</p>
        ) : null}
      </form>
      {onlineQuery.data?.note ? (
        <p className="text-sm text-muted-foreground">{onlineQuery.data.note}</p>
      ) : null}

      {packsQuery.isPending ? (
        <p className="text-sm text-muted-foreground">正在加载方案包…</p>
      ) : packs.length === 0 ? (
        <p className="text-sm text-muted-foreground">还没有方案包。</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {packs.map((p) => (
            <article key={p.id} className="flex flex-col gap-3 rounded-lg border p-3">
              <header className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-medium">{p.name}</h3>
                <div className="flex shrink-0 flex-wrap justify-end gap-1">
                  <Badge variant="secondary">{ORIGIN_NAMES[p.origin] ?? p.origin}</Badge>
                  {p.availability !== "available" ? (
                    <Badge variant="destructive">
                      {AVAILABILITY_NAMES[p.availability] ?? p.availability}
                    </Badge>
                  ) : null}
                </div>
              </header>
              <p className="text-xs text-muted-foreground">
                {p.vertical} · v{p.version} · {p.author || "匿名"}
              </p>
              <p className="text-sm">{p.tagline || p.description}</p>
              {p.unavailable_reason ? (
                <p className="text-xs text-destructive">{p.unavailable_reason}</p>
              ) : null}
              <p className="text-xs text-muted-foreground">
                {p.camera_count} 路机位 · {p.rule_count} 条规则 · {p.scene_count} 个场景
              </p>
              <div className="mt-auto flex flex-wrap items-center gap-2">
                <Badge variant="outline">{experienceBadge(p)}</Badge>
                <div className="flex flex-1 justify-end gap-2">
                  {p.origin === "installed" ? (
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => uninstall.mutate(p.id)}
                      disabled={uninstall.isPending}
                    >
                      卸载
                    </Button>
                  ) : null}
                  <Button size="sm" render={<Link prefetch={false} href={packDetailPath(p.id)} />}>
                    查看详情
                  </Button>
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
