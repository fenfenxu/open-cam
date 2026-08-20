import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import { DataTable, type DataTableColumn } from "@/components/app/data-table";
import { PageHeader } from "@/components/app/page-header";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { RULE_TYPE_NAMES, PERSON_CHANNEL_KINDS } from "@/lib/labels";
import { themeForSsr } from "@/lib/theme";
import {
  VLM_PRESETS,
  KIMI_CODE_MODEL_OPTIONS,
  matchVlmPreset,
  type AccountStatus,
  type NotifyChannel,
  type SystemInfo,
  type VlmConfig,
} from "@/lib/system";

const ALL = "__all__";

const THEMES = [
  { value: "light", label: "浅色", icon: Sun },
  { value: "dark", label: "深色", icon: Moon },
  { value: "system", label: "跟随系统", icon: Monitor },
] as const;

type PersonRow = { id: number; name: string; login_name: string | null };
type EventRoutingRow = {
  id: number;
  person_id: number;
  camera_id: number | null;
  rule_type: string | null;
  enabled: boolean;
};

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "请求失败";
}

export function SettingsPage() {
  const { theme, setTheme } = useTheme();
  const [themeMounted, setThemeMounted] = useState(false);
  const queryClient = useQueryClient();
  const [preset, setPreset] = useState("custom");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [nName, setNName] = useState("");
  const [nWebhook, setNWebhook] = useState("");
  const [nCamera, setNCamera] = useState(ALL);
  const [nRule, setNRule] = useState(ALL);
  const [pName, setPName] = useState("");
  const [pLogin, setPLogin] = useState("");
  const [pWebhook, setPWebhook] = useState("");
  const [pKind, setPKind] = useState("feishu");
  const [pCamera, setPCamera] = useState(ALL);
  const [pRule, setPRule] = useState(ALL);
  const syncedVlm = useRef(false);
  const selectedVlmPreset = VLM_PRESETS.find((item) => item.id === preset);
  const isKimiCode = selectedVlmPreset?.id === "kimi-code";

  const infoQuery = useQuery({
    queryKey: ["system-info"],
    queryFn: () => api<SystemInfo>("/api/system/info"),
  });
  const vlmQuery = useQuery({
    queryKey: ["system-vlm"],
    queryFn: () => api<VlmConfig>("/api/system/vlm"),
  });
  const acctQuery = useQuery({
    queryKey: ["account-status"],
    queryFn: () => api<AccountStatus>("/api/account/status"),
  });
  const channelsQuery = useQuery({
    queryKey: ["notify-channels"],
    queryFn: () => api<NotifyChannel[]>("/api/notify-channels"),
  });
  const peopleQuery = useQuery({
    queryKey: ["people"],
    queryFn: () => api<PersonRow[]>("/api/people"),
  });
  const routingsQuery = useQuery({
    queryKey: ["event-routings"],
    queryFn: () => api<EventRoutingRow[]>("/api/event-routings"),
  });
  const camerasQuery = useQuery({
    queryKey: ["cameras"],
    queryFn: () => api<Camera[]>("/api/cameras"),
  });

  const cameras = camerasQuery.data ?? [];
  const channels = channelsQuery.data ?? [];
  const people = peopleQuery.data ?? [];
  const routings = routingsQuery.data ?? [];
  const vlm = vlmQuery.data;

  useEffect(() => setThemeMounted(true), []);

  useEffect(() => {
    if (infoQuery.isError) toast.error(errorMessage(infoQuery.error));
  }, [infoQuery.isError, infoQuery.error]);
  useEffect(() => {
    if (vlmQuery.isError) toast.error(errorMessage(vlmQuery.error));
  }, [vlmQuery.isError, vlmQuery.error]);
  useEffect(() => {
    if (acctQuery.isError) toast.error(errorMessage(acctQuery.error));
  }, [acctQuery.isError, acctQuery.error]);
  useEffect(() => {
    if (channelsQuery.isError) toast.error(errorMessage(channelsQuery.error));
  }, [channelsQuery.isError, channelsQuery.error]);

  useEffect(() => {
    if (!vlm || syncedVlm.current) return;
    syncedVlm.current = true;
    setPreset(matchVlmPreset(vlm.base_url));
    setBaseUrl(vlm.base_url || "");
    setModel(vlm.model || "");
  }, [vlm]);

  const saveVlm = useMutation({
    mutationFn: (payload: Record<string, string>) =>
      api("/api/system/vlm", jsonBody("PUT", payload)),
    onSuccess: async () => {
      toast.success("已保存");
      setApiKey("");
      syncedVlm.current = false;
      await queryClient.invalidateQueries({ queryKey: ["system-vlm"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const testVlm = useMutation({
    mutationFn: () => api<{ ok: boolean; model?: string }>("/api/system/vlm/test", { method: "POST" }),
    onSuccess: (r) => toast.success(r.ok ? `连接成功（${r.model}）` : "测试失败"),
    onError: (err) => toast.error(errorMessage(err)),
  });

  const clearVlm = useMutation({
    mutationFn: () => api("/api/system/vlm", jsonBody("PUT", { api_key: "" })),
    onSuccess: async () => {
      toast.success("已清除本机 Key");
      syncedVlm.current = false;
      await queryClient.invalidateQueries({ queryKey: ["system-vlm"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const addChannel = useMutation({
    mutationFn: () =>
      api(
        "/api/notify-channels",
        jsonBody("POST", {
          name: nName.trim(),
          webhook: nWebhook.trim(),
          camera_id: nCamera === ALL ? null : Number(nCamera),
          rule_type: nRule === ALL ? null : nRule,
        }),
      ),
    onSuccess: async () => {
      toast.success("已添加");
      setNName("");
      setNWebhook("");
      setNCamera(ALL);
      setNRule(ALL);
      await queryClient.invalidateQueries({ queryKey: ["notify-channels"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const deleteChannel = useMutation({
    mutationFn: (id: number) => api(`/api/notify-channels/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      toast.success("已删除");
      await queryClient.invalidateQueries({ queryKey: ["notify-channels"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const testChannel = useMutation({
    mutationFn: (id: number) =>
      api<{ ok: boolean; error?: string }>(`/api/notify-channels/${id}/test`, { method: "POST" }),
    onSuccess: (r) => {
      if (r.ok) toast.success("测试推送成功");
      else toast.error(`推送失败：${r.error}`);
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const toggleChannel = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      api(`/api/notify-channels/${id}`, jsonBody("PATCH", { enabled })),
    onError: (err) => toast.error(errorMessage(err)),
  });

  const addPerson = useMutation({
    mutationFn: async () => {
      const person = await api<PersonRow>(
        "/api/people",
        jsonBody("POST", {
          name: pName.trim(),
          login_name: pLogin.trim() || null,
        }),
      );
      if (pWebhook.trim()) {
        await api(
          `/api/people/${person.id}/channels`,
          jsonBody("POST", { kind: pKind, webhook: pWebhook.trim() }),
        );
      }
      await api(
        "/api/event-routings",
        jsonBody("POST", {
          person_id: person.id,
          camera_id: pCamera === ALL ? null : Number(pCamera),
          rule_type: pRule === ALL ? null : pRule,
        }),
      );
    },
    onSuccess: async () => {
      toast.success("员工已添加");
      setPName("");
      setPLogin("");
      setPWebhook("");
      setPCamera(ALL);
      setPRule(ALL);
      await queryClient.invalidateQueries({ queryKey: ["people"] });
      await queryClient.invalidateQueries({ queryKey: ["event-routings"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  const deletePerson = useMutation({
    mutationFn: (id: number) => api(`/api/people/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      toast.success("已删除员工");
      await queryClient.invalidateQueries({ queryKey: ["people"] });
      await queryClient.invalidateQueries({ queryKey: ["event-routings"] });
    },
    onError: (err) => toast.error(errorMessage(err)),
  });

  function camName(id: number | null) {
    if (id == null) return "全部摄像头";
    const c = cameras.find((x) => x.id === id);
    return c ? `[${c.id}] ${c.name}` : `#${id}`;
  }

  const columns = useMemo<DataTableColumn<NotifyChannel>[]>(
    () => [
      { accessorKey: "name", header: "名称" },
      {
        accessorKey: "webhook",
        header: "Webhook",
        cell: ({ row }) => (
          <span className="block max-w-xs truncate font-mono text-xs">{row.original.webhook}</span>
        ),
      },
      {
        id: "scope",
        header: "适用范围",
        cell: ({ row }) =>
          `${camName(row.original.camera_id)} · ${RULE_TYPE_NAMES[row.original.rule_type || ""] || "全部类型"}`,
      },
      {
        id: "enabled",
        header: "启用",
        cell: ({ row }) => (
          <Checkbox
            checked={row.original.enabled}
            onCheckedChange={(checked) =>
              toggleChannel.mutate({ id: row.original.id, enabled: checked === true })
            }
          />
        ),
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-1">
            <Button size="xs" variant="outline" onClick={() => testChannel.mutate(row.original.id)}>
              测试
            </Button>
            <Button
              size="xs"
              variant="destructive"
              onClick={() => deleteChannel.mutate(row.original.id)}
            >
              删除
            </Button>
          </div>
        ),
      },
    ],
    [cameras, deleteChannel, testChannel, toggleChannel],
  );

  const info = infoQuery.data;
  const acct = acctQuery.data;
  const vlmStatus = vlm?.configured
    ? `已配置${vlm.api_key_hint ? `（${vlm.api_key_hint}）` : ""}，来源：${vlm.api_key_source === "env" ? "环境变量" : "本页保存"}`
    : "未配置，训练解析和事件复核都需要它";
  const currentTheme =
    THEMES.find((item) => item.value === themeForSsr(theme, themeMounted)) ?? THEMES[2];
  const ThemeIcon = currentTheme.icon;

  return (
    <div className="space-y-6">
      <PageHeader title="设置" />

      <section className="space-y-3 rounded-lg border p-4">
        <div>
          <h2 className="text-lg font-medium">外观</h2>
          <p className="text-sm text-muted-foreground">选择控制台的显示主题。</p>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
            <ThemeIcon />
            主题：{currentTheme.label}
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            {THEMES.map((item) => (
              <DropdownMenuItem key={item.value} onClick={() => setTheme(item.value)}>
                <item.icon />
                {item.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <h2 className="text-lg font-medium">系统信息</h2>
        {infoQuery.isPending ? (
          <p className="text-sm text-muted-foreground">正在加载…</p>
        ) : info ? (
          <dl className="grid gap-2 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-muted-foreground">版本</dt>
              <dd>{info.version}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">推理设备</dt>
              <dd>
                {info.device}（配置：{info.device_config}）
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">系统内存</dt>
              <dd>{info.memory_total_gb ?? "未知"} GB</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">显存</dt>
              <dd>
                {info.vram_total_gb ?? "—"} {info.vram_total_gb ? "GB" : ""}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">检测器</dt>
              <dd>
                {info.detector}（{info.yolo_model}）
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">采样帧率</dt>
              <dd>{info.detect_fps} fps</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">方案包</dt>
              <dd>
                可用 {info.packs_available} 个，其中已安装 {info.packs_installed} 个
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">数据目录</dt>
              <dd className="font-mono text-xs">{info.data_dir || ""}</dd>
            </div>
          </dl>
        ) : (
          <p className="text-sm text-muted-foreground">系统信息获取失败</p>
        )}
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <h2 className="text-lg font-medium">大模型</h2>
        <p className="text-sm text-muted-foreground">
          训练时理解你的需求、自动标注、告警复核都走这里。Key 只存在这台电脑的数据目录，不会进代码仓库。
        </p>
        {vlm?.env_locked ? (
          <p className="text-sm">
            当前生效的是环境变量 OPENCAM_VLM_API_KEY，本页保存的 Key 不会覆盖它。
          </p>
        ) : null}
        <p className="text-sm">{vlmQuery.isPending ? "正在加载…" : vlmStatus}</p>
        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label>服务商</Label>
            <Select
              value={preset}
              onValueChange={(v) => {
                if (!v) return;
                const id = String(v);
                setPreset(id);
                const p = VLM_PRESETS.find((x) => x.id === id);
                if (!p || p.id === "custom") return;
                setBaseUrl(p.base_url);
                setModel(p.model);
              }}
            >
              <SelectTrigger className="w-full max-w-lg">
                <SelectValue>
                  {VLM_PRESETS.find((item) => item.id === preset)?.name || "自定义"}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {VLM_PRESETS.map((p) => (
                  <SelectItem key={p.id} value={p.id}>
                    {p.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="vlm-url">接口地址</Label>
            <Input
              id="vlm-url"
              value={baseUrl}
              onChange={(e) => {
                const value = e.target.value;
                setBaseUrl(value);
                setPreset(matchVlmPreset(value));
              }}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="vlm-model">模型名</Label>
            {isKimiCode ? (
              <Select value={model} onValueChange={(value) => value && setModel(value)}>
                <SelectTrigger id="vlm-model" className="w-full max-w-lg">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {KIMI_CODE_MODEL_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>
                      {option.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <Input
                id="vlm-model"
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            )}
            {isKimiCode ? (
              <p className="text-xs text-muted-foreground">
                仅允许 Kimi Code 官方模型 ID；具体可用项取决于会员套餐。
              </p>
            ) : null}
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="vlm-key">API Key</Label>
            <Input
              id="vlm-key"
              type="password"
              autoComplete="off"
              value={apiKey}
              disabled={vlm?.env_locked}
              placeholder={
                vlm?.api_key_hint ? `不改请留空，已保存 ${vlm.api_key_hint}` : "粘贴 API Key"
              }
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => {
                if (!baseUrl.trim() || !model.trim()) {
                  toast.error("请填写接口地址和模型名");
                  return;
                }
                if (!apiKey.trim() && !vlm?.configured) {
                  toast.error("请填写 API Key");
                  return;
                }
                const payload: Record<string, string> = {
                  base_url: baseUrl.trim(),
                  model: model.trim(),
                };
                if (apiKey.trim()) payload.api_key = apiKey.trim();
                saveVlm.mutate(payload);
              }}
              disabled={saveVlm.isPending}
            >
              保存
            </Button>
            <Button variant="outline" onClick={() => testVlm.mutate()} disabled={testVlm.isPending}>
              测试连接
            </Button>
            <Button
              variant="destructive"
              disabled={vlm?.env_locked || clearVlm.isPending}
              onClick={() => clearVlm.mutate()}
            >
              清除本机 Key
            </Button>
          </div>
        </div>
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <h2 className="text-lg font-medium">平台账号</h2>
        {acctQuery.isPending ? (
          <p className="text-sm text-muted-foreground">正在加载…</p>
        ) : acct ? (
          <>
            <dl className="grid gap-2 text-sm sm:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">平台</dt>
                <dd>{acct.platform_base_url || "未配置"}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">登录状态</dt>
                <dd>{acct.logged_in ? "已登录" : "未登录"}</dd>
              </div>
            </dl>
            {acct.note ? <p className="text-sm text-muted-foreground">{acct.note}</p> : null}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">账号状态获取失败</p>
        )}
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <h2 className="text-lg font-medium">通知渠道</h2>
        <p className="text-sm text-muted-foreground">
          事件命中后自动推送到 webhook（兼容飞书 / 企业微信 / 钉钉机器人）；适用范围留空表示全部。
        </p>
        {channelsQuery.isPending ? (
          <p className="text-sm text-muted-foreground">正在加载…</p>
        ) : (
          <DataTable
            columns={columns}
            data={channels}
            getRowId={(row) => String(row.id)}
            emptyMessage="还没有通知渠道。"
          />
        )}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!nName.trim() || !nWebhook.trim()) {
              toast.error("请填写名称和 webhook URL");
              return;
            }
            addChannel.mutate();
          }}
        >
          <Input
            className="w-36"
            placeholder="联系人/渠道名"
            value={nName}
            onChange={(e) => setNName(e.target.value)}
          />
          <Input
            className="min-w-56 flex-1"
            placeholder="webhook URL"
            value={nWebhook}
            onChange={(e) => setNWebhook(e.target.value)}
          />
          <Select value={nCamera} onValueChange={(v) => v && setNCamera(String(v))}>
            <SelectTrigger className="w-44">
              <SelectValue>
                {nCamera === ALL
                  ? "全部摄像头"
                  : (() => {
                      const camera = cameras.find((item) => String(item.id) === nCamera);
                      return camera ? `[${camera.id}] ${camera.name}` : "选择摄像头";
                    })()}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>全部摄像头</SelectItem>
              {cameras.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  [{c.id}] {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={nRule} onValueChange={(v) => v && setNRule(String(v))}>
            <SelectTrigger className="w-36">
              <SelectValue>{nRule === ALL ? "全部类型" : RULE_TYPE_NAMES[nRule] || "选择类型"}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>全部类型</SelectItem>
              {Object.entries(RULE_TYPE_NAMES).map(([k, v]) => (
                <SelectItem key={k} value={k}>
                  {v}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="submit" disabled={addChannel.isPending}>
            添加
          </Button>
        </form>
      </section>

      <section className="space-y-3 rounded-lg border p-4">
        <h2 className="text-lg font-medium">员工与路由</h2>
        <p className="text-sm text-muted-foreground">
          新建待办时按路由匹配员工并推送个人 webhook；群机器人在上方兜底。登录名可选。
        </p>
        {peopleQuery.isPending ? (
          <p className="text-sm text-muted-foreground">正在加载…</p>
        ) : (
          <DataTable
            columns={[
              { accessorKey: "name", header: "姓名" },
              {
                accessorKey: "login_name",
                header: "登录名",
                cell: ({ row }) => row.original.login_name || "—",
              },
              {
                id: "routing",
                header: "路由",
                cell: ({ row }) => {
                  const rs = routings.filter((r) => r.person_id === row.original.id);
                  if (!rs.length) return "—";
                  return rs
                    .map(
                      (r) =>
                        `${camName(r.camera_id)} · ${RULE_TYPE_NAMES[r.rule_type || ""] || "全部类型"}`,
                    )
                    .join("；");
                },
              },
              {
                id: "actions",
                header: "",
                cell: ({ row }) => (
                  <Button
                    size="xs"
                    variant="destructive"
                    onClick={() => deletePerson.mutate(row.original.id)}
                  >
                    删除
                  </Button>
                ),
              },
            ]}
            data={people}
            getRowId={(row) => String(row.id)}
            emptyMessage="还没有员工。"
          />
        )}
        <form
          className="flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!pName.trim()) {
              toast.error("请填写员工姓名");
              return;
            }
            addPerson.mutate();
          }}
        >
          <Input
            className="w-32"
            placeholder="姓名"
            value={pName}
            onChange={(e) => setPName(e.target.value)}
          />
          <Input
            className="w-32"
            placeholder="登录名（可选）"
            value={pLogin}
            onChange={(e) => setPLogin(e.target.value)}
          />
          <Select value={pKind} onValueChange={(v) => v && setPKind(String(v))}>
            <SelectTrigger className="w-28">
              <SelectValue>{PERSON_CHANNEL_KINDS[pKind] || "选择渠道"}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {Object.entries(PERSON_CHANNEL_KINDS).map(([k, v]) => (
                <SelectItem key={k} value={k}>
                  {v}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Input
            className="min-w-48 flex-1"
            placeholder="个人 webhook（可选）"
            value={pWebhook}
            onChange={(e) => setPWebhook(e.target.value)}
          />
          <Select value={pCamera} onValueChange={(v) => v && setPCamera(String(v))}>
            <SelectTrigger className="w-44">
              <SelectValue>
                {pCamera === ALL
                  ? "全部摄像头"
                  : (() => {
                      const camera = cameras.find((item) => String(item.id) === pCamera);
                      return camera ? `[${camera.id}] ${camera.name}` : "选择摄像头";
                    })()}
              </SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>全部摄像头</SelectItem>
              {cameras.map((c) => (
                <SelectItem key={c.id} value={String(c.id)}>
                  [{c.id}] {c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={pRule} onValueChange={(v) => v && setPRule(String(v))}>
            <SelectTrigger className="w-36">
              <SelectValue>{pRule === ALL ? "全部类型" : RULE_TYPE_NAMES[pRule] || "选择类型"}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>全部类型</SelectItem>
              {Object.entries(RULE_TYPE_NAMES).map(([k, v]) => (
                <SelectItem key={k} value={k}>
                  {v}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button type="submit" disabled={addPerson.isPending}>
            添加员工
          </Button>
        </form>
      </section>
    </div>
  );
}
