import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { PageHeader } from "@/components/app/page-header";
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
import { isLegacyPack, type PackApplyResult, type SolutionPack } from "@/lib/packs";

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

export function MarketplacePage() {
  const queryClient = useQueryClient();
  const [source, setSource] = useState("");
  const [legacyCam, setLegacyCam] = useState<Record<string, string>>({});

  const packsQuery = useQuery({
    queryKey: ["packs"],
    queryFn: () => api<SolutionPack[]>("/api/packs"),
  });
  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/cameras"),
  });
  const onlineQuery = useQuery({
    queryKey: ["packs-online"],
    queryFn: () => api<{ note?: string }>("/api/packs/online"),
  });

  const cameras = camerasQuery.data ?? [];
  const packs = packsQuery.data ?? [];

  useEffect(() => {
    if (packsQuery.isError) toast.error(errorMessage(packsQuery.error));
  }, [packsQuery.isError, packsQuery.error]);

  useEffect(() => {
    if (!cameras.length) return;
    setLegacyCam((prev) => {
      const next = { ...prev };
      for (const p of packs) {
        if (isLegacyPack(p) && !next[p.id]) next[p.id] = String(cameras[0].id);
      }
      return next;
    });
  }, [packs, cameras]);

  const install = useMutation({
    mutationFn: () =>
      api<SolutionPack>("/api/packs/install", jsonBody("POST", { source: source.trim() })),
    onSuccess: async (pack) => {
      toast.success(`已安装：${pack.name}`);
      setSource("");
      await queryClient.invalidateQueries({ queryKey: ["packs"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const apply = useMutation({
    mutationFn: async (pack: SolutionPack) => {
      const body = isLegacyPack(pack)
        ? { camera_id: Number(legacyCam[pack.id]) }
        : {};
      return {
        pack,
        data: await api<PackApplyResult>(`/api/packs/${pack.id}/apply`, jsonBody("POST", body)),
      };
    },
    onSuccess: async ({ pack, data }) => {
      if (isLegacyPack(pack)) {
        const names = (data.rules || []).map((r) => RULE_TYPE_NAMES[r.type] || r.type).join("、");
        toast.success(`已应用 ${data.rules?.length ?? 0} 条规则（含：${names}），可到「规则」页调整`);
      } else {
        toast.success(
          `已创建 ${data.cameras?.length ?? 0} 路摄像头，请到「摄像头」页改成真实源后再启动`,
        );
      }
      await queryClient.invalidateQueries({ queryKey: ["packs"] });
      await queryClient.invalidateQueries({ queryKey: ["cameras"] });
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
      <PageHeader title="方案市场" description="浏览内置/已安装包，一键应用、安装或卸载。" />

      <form
        className="flex flex-wrap items-end gap-3 rounded-lg border p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!source.trim()) {
            toast.error("请填写安装源");
            return;
          }
          install.mutate();
        }}
      >
        <div className="grid min-w-64 flex-1 gap-1.5">
          <Label htmlFor="install-src">安装源</Label>
          <Input
            id="install-src"
            value={source}
            onChange={(e) => setSource(e.target.value)}
            placeholder="本地目录 / pack.zip 路径 / https://... 包地址"
          />
        </div>
        <Button type="submit" disabled={install.isPending}>
          安装
        </Button>
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
            <article key={p.id} className="space-y-3 rounded-lg border p-3">
              <header className="flex items-start justify-between gap-2">
                <h3 className="text-sm font-medium">{p.name}</h3>
                <Badge variant="secondary">{p.origin === "builtin" ? "内置" : "已安装"}</Badge>
              </header>
              <p className="text-xs text-muted-foreground">
                {p.vertical} · v{p.version} · {p.author || "匿名"}
              </p>
              <p className="text-sm">{p.description}</p>
              {isLegacyPack(p) ? (
                <>
                  <p className="text-xs text-muted-foreground">
                    规则模板：{(p.rules || []).map((r) => r.name).join("、")}
                  </p>
                  <Select
                    value={legacyCam[p.id] ?? ""}
                    onValueChange={(v) => v && setLegacyCam((prev) => ({ ...prev, [p.id]: String(v) }))}
                    disabled={!cameras.length}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue placeholder="选择摄像头" />
                    </SelectTrigger>
                    <SelectContent>
                      {cameras.map((c) => (
                        <SelectItem key={c.id} value={String(c.id)}>
                          应用到：[{c.id}] {c.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </>
              ) : (
                <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                  {(p.cameras || []).map((c) => {
                    const names = (p.rules || [])
                      .filter((r) => r.camera === c.id)
                      .map((r) => r.name)
                      .join("、");
                    return (
                      <li key={c.id}>
                        {c.name}
                        {names ? `：${names}` : ""}
                      </li>
                    );
                  })}
                </ul>
              )}
              <div className="flex flex-wrap gap-2">
                <Button
                  size="sm"
                  disabled={(isLegacyPack(p) && !cameras.length) || apply.isPending}
                  onClick={() => apply.mutate(p)}
                >
                  应用
                </Button>
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
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
