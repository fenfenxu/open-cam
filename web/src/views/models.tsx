"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, Check, Pencil, Plus, Search, Upload, X } from "lucide-react";
import { useState } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import {
  MODEL_DISTRIBUTION_TYPES,
  MODEL_KINDS,
  MODEL_ORIGIN_TYPES,
  modelDistributionLabel,
  modelKindLabel,
  modelOriginLabel,
  type AnalysisProfile,
  type ModelBinding,
  type ModelAsset,
  type ModelDistributionType,
  type ModelKind,
  type ModelOriginType,
} from "@/lib/models";

function errorMessage(error: unknown): string {
  return error instanceof ApiError || error instanceof Error ? error.message : "请求失败";
}

function parseCapabilities(text: string): string[] {
  return text.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
}

export function ModelsPage() {
  const queryClient = useQueryClient();
  const [editingId, setEditingId] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [originType, setOriginType] = useState<ModelOriginType>("uploaded");
  const [distributionType, setDistributionType] = useState<ModelDistributionType>("private");
  const [modelKind, setModelKind] = useState<ModelKind>("object_detection");
  const [capabilities, setCapabilities] = useState("");
  const [taskKey, setTaskKey] = useState("");
  const [solutionPackId, setSolutionPackId] = useState("");

  const [filterOrigin, setFilterOrigin] = useState("all");
  const [filterDistribution, setFilterDistribution] = useState("all");
  const [filterKind, setFilterKind] = useState("all");
  const [search, setSearch] = useState("");

  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [uploadDescription, setUploadDescription] = useState("");
  const [recommendStageId, setRecommendStageId] = useState("");

  const listParams = new URLSearchParams();
  if (filterOrigin !== "all") listParams.set("origin_type", filterOrigin);
  if (filterDistribution !== "all") listParams.set("distribution_type", filterDistribution);
  if (filterKind !== "all") listParams.set("model_kind", filterKind);
  if (search.trim()) listParams.set("q", search.trim());
  const listUrl = `/api/models/assets${listParams.size ? `?${listParams}` : ""}`;

  const assetsQuery = useQuery({
    queryKey: ["model-assets", listUrl],
    queryFn: () => api<ModelAsset[]>(listUrl),
  });

  const profilesQuery = useQuery({
    queryKey: ["analysis-profiles"],
    queryFn: () => api<AnalysisProfile[]>("/api/analysis-profiles"),
  });
  const pendingBindingsQuery = useQuery({
    queryKey: ["model-bindings", "pending"],
    queryFn: () => api<ModelBinding[]>(
      "/api/model-bindings?relation_source=ai_recommended&relation_status=pending",
    ),
  });

  const resetForm = () => {
    setEditingId(null);
    setName("");
    setDescription("");
    setOriginType("uploaded");
    setDistributionType("private");
    setModelKind("object_detection");
    setCapabilities("");
    setTaskKey("");
    setSolutionPackId("");
  };

  const saveAsset = useMutation({
    mutationFn: () => {
      const payload = {
        name: name.trim(),
        description: description.trim(),
        origin_type: originType,
        distribution_type: distributionType,
        model_kind: modelKind,
        capabilities: parseCapabilities(capabilities),
        task_key: taskKey.trim() || null,
        solution_pack_id: solutionPackId.trim() || null,
      };
      if (editingId !== null) {
        return api<ModelAsset>(`/api/models/assets/${editingId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      return api<ModelAsset>("/api/models/assets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    },
    onSuccess: async () => {
      toast.success(editingId !== null ? "模型资产已更新" : "模型资产已登记");
      resetForm();
      await queryClient.invalidateQueries({ queryKey: ["model-assets"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const uploadAsset = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", uploadFile!);
      form.append("name", uploadName.trim());
      form.append("description", uploadDescription.trim());
      form.append("model_kind", modelKind);
      form.append("capabilities", capabilities);
      return api<{ asset: ModelAsset }>("/api/models/assets/upload", {
        method: "POST",
        body: form,
      });
    },
    onSuccess: async () => {
      toast.success("模型已上传并登记");
      setUploadFile(null);
      setUploadName("");
      setUploadDescription("");
      await queryClient.invalidateQueries({ queryKey: ["model-assets"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const toggleStatus = useMutation({
    mutationFn: (asset: ModelAsset) =>
      api<ModelAsset>(`/api/models/assets/${asset.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: asset.status === "archived" ? "active" : "archived" }),
      }),
    onSuccess: async (asset) => {
      toast.success(asset.status === "archived" ? "模型已归档" : "模型已恢复");
      await queryClient.invalidateQueries({ queryKey: ["model-assets"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const recommend = useMutation({
    mutationFn: () => api<ModelBinding[]>("/api/model-bindings/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_type: "pipeline_stage",
        target_id: Number(recommendStageId),
        limit: 5,
      }),
    }),
    onSuccess: async (bindings) => {
      toast.success(bindings.length ? `已生成 ${bindings.length} 条待审核推荐` : "目标已有人工关联，未生成 AI 推荐");
      await queryClient.invalidateQueries({ queryKey: ["model-bindings", "pending"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const reviewBinding = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "confirm" | "reject" }) =>
      api<ModelBinding>(`/api/model-bindings/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }),
    onSuccess: async (_, variables) => {
      toast.success(variables.action === "confirm" ? "模型关联已确认" : "推荐已拒绝");
      await queryClient.invalidateQueries({ queryKey: ["model-bindings", "pending"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const startEdit = (asset: ModelAsset) => {
    setEditingId(asset.id);
    setName(asset.name);
    setDescription(asset.description);
    setOriginType(asset.origin_type);
    setDistributionType(asset.distribution_type);
    setModelKind(asset.model_kind);
    setCapabilities(asset.capabilities.join(", "));
    setTaskKey(asset.task_key ?? "");
    setSolutionPackId(asset.solution_pack_id ?? "");
  };

  const assets = assetsQuery.data ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader
        title="模型管理"
        description="登记模型名称、描述、来源、交付方式和能力。模型资产与规则关联独立管理。"
      />

      <section className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <Plus className="size-4" />
          <h2 className="font-medium">{editingId !== null ? "编辑模型资产" : "登记模型资产"}</h2>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="model-name">模型名称</Label>
            <Input id="model-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：门店人员检测模型" />
          </div>
          <div className="space-y-2">
            <Label>模型来源</Label>
            <Select value={originType} onValueChange={(value) => value && setOriginType(value as ModelOriginType)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>{MODEL_ORIGIN_TYPES.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>交付方式</Label>
            <Select value={distributionType} onValueChange={(value) => value && setDistributionType(value as ModelDistributionType)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>{MODEL_DISTRIBUTION_TYPES.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>模型类型</Label>
            <Select value={modelKind} onValueChange={(value) => value && setModelKind(value as ModelKind)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>{MODEL_KINDS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="model-capabilities">能力标签（逗号分隔，可选）</Label>
            <Input id="model-capabilities" value={capabilities} onChange={(event) => setCapabilities(event.target.value)} placeholder="例如：person_detection, person.box" />
          </div>
          <div className="space-y-2">
            <Label htmlFor="model-task-key">能力标识（可选）</Label>
            <Input id="model-task-key" value={taskKey} onChange={(event) => setTaskKey(event.target.value)} placeholder="例如：person_detection" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="model-description">模型描述</Label>
            <Textarea id="model-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述适用场景、识别目标、限制条件等，后续可用于 AI 推荐关联。" />
          </div>
          {distributionType === "solution" ? (
            <div className="space-y-2">
              <Label htmlFor="solution-pack-id">方案标识（可选）</Label>
              <Input id="solution-pack-id" value={solutionPackId} onChange={(event) => setSolutionPackId(event.target.value)} placeholder="例如：fast-food" />
            </div>
          ) : null}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          {editingId !== null ? (
            <Button variant="outline" onClick={resetForm}>取消编辑</Button>
          ) : null}
          <Button disabled={!name.trim() || saveAsset.isPending} onClick={() => saveAsset.mutate()}>
            <BrainCircuit />
            {editingId !== null ? "保存修改" : "登记模型"}
          </Button>
        </div>
      </section>

      <section className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="mb-1 flex items-center gap-2">
          <BrainCircuit className="size-4" />
          <h2 className="font-medium">关联推荐与人工审核</h2>
        </div>
        <p className="mb-4 text-sm text-muted-foreground">
          推荐只保存为待审核关系，不会覆盖人工关联，也不会自动上线模型。
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="min-w-64 space-y-2">
            <Label htmlFor="recommend-stage">推荐到分析阶段</Label>
            <Select value={recommendStageId} onValueChange={(value) => value && setRecommendStageId(value)}>
              <SelectTrigger id="recommend-stage"><SelectValue placeholder="选择分析阶段" /></SelectTrigger>
              <SelectContent>
                {(profilesQuery.data ?? []).flatMap((profile) => profile.stages.map((stage) => (
                  <SelectItem key={stage.id} value={String(stage.id)}>
                    {profile.name} / {stage.name}
                  </SelectItem>
                )))}
              </SelectContent>
            </Select>
          </div>
          <Button disabled={!recommendStageId || recommend.isPending} onClick={() => recommend.mutate()}>
            <BrainCircuit />
            生成推荐
          </Button>
        </div>
        <div className="mt-5 space-y-3">
          {(pendingBindingsQuery.data ?? []).length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无待审核推荐。</p>
          ) : (pendingBindingsQuery.data ?? []).map((binding) => {
            const asset = assets.find((item) => item.id === binding.model_asset_id);
            return (
              <div key={binding.id} className="rounded-lg border p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="space-y-1 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{asset?.name ?? `模型资产 #${binding.model_asset_id}`}</span>
                      <Badge variant="secondary">待人工确认</Badge>
                      {binding.confidence !== null ? <Badge variant="outline">置信度 {(binding.confidence * 100).toFixed(0)}%</Badge> : null}
                    </div>
                    <p className="text-muted-foreground">{binding.reason || "暂无推荐理由"}</p>
                    {binding.warnings.length > 0 ? <p className="text-muted-foreground">提示：{binding.warnings.join("；")}</p> : null}
                  </div>
                  <div className="flex shrink-0 gap-2">
                    <Button size="sm" disabled={reviewBinding.isPending} onClick={() => reviewBinding.mutate({ id: binding.id, action: "confirm" })}>
                      <Check />
                      确认关联
                    </Button>
                    <Button size="sm" variant="outline" disabled={reviewBinding.isPending} onClick={() => reviewBinding.mutate({ id: binding.id, action: "reject" })}>
                      <X />
                      拒绝推荐
                    </Button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <Upload className="size-4" />
          <h2 className="font-medium">上传模型产物</h2>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="upload-file">权重文件</Label>
            <Input
              id="upload-file"
              type="file"
              onChange={(event) => {
                const file = event.target.files?.[0] ?? null;
                setUploadFile(file);
                if (file && !uploadName) setUploadName(file.name.replace(/\.[^.]+$/, ""));
              }}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="upload-name">模型名称</Label>
            <Input id="upload-name" value={uploadName} onChange={(event) => setUploadName(event.target.value)} placeholder="例如：安全帽检测模型" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="upload-description">模型描述（可选）</Label>
            <Input id="upload-description" value={uploadDescription} onChange={(event) => setUploadDescription(event.target.value)} placeholder="上传后自动生成带哈希的模型版本" />
          </div>
        </div>
        <div className="mt-4 flex justify-end">
          <Button disabled={!uploadFile || !uploadName.trim() || uploadAsset.isPending} onClick={() => uploadAsset.mutate()}>
            <Upload />
            上传并登记
          </Button>
        </div>
      </section>

      <section className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <h2 className="font-medium">已登记模型（{assets.length}）</h2>
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <Select value={filterOrigin} onValueChange={(value) => value && setFilterOrigin(value)}>
              <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部来源</SelectItem>
                {MODEL_ORIGIN_TYPES.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={filterDistribution} onValueChange={(value) => value && setFilterDistribution(value)}>
              <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部交付</SelectItem>
                {MODEL_DISTRIBUTION_TYPES.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={filterKind} onValueChange={(value) => value && setFilterKind(value)}>
              <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部类型</SelectItem>
                {MODEL_KINDS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <div className="relative">
              <Search className="absolute left-2 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input className="w-48 pl-8" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称或描述" />
            </div>
          </div>
        </div>
        {assets.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">还没有模型资产。</p>
        ) : (
          <div className="space-y-3">
            {assets.map((asset) => (
              <div key={asset.id} className="rounded-lg border p-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="font-medium">{asset.name}</h3>
                      <Badge variant="secondary">{modelOriginLabel(asset.origin_type)}</Badge>
                      <Badge variant="secondary">{modelDistributionLabel(asset.distribution_type)}</Badge>
                      <Badge variant="outline">{modelKindLabel(asset.model_kind)}</Badge>
                      {asset.status === "archived" ? <Badge variant="outline">已归档</Badge> : null}
                    </div>
                    <p className="text-sm text-muted-foreground">{asset.description || "暂无描述"}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Button variant="ghost" size="sm" onClick={() => startEdit(asset)}>
                      <Pencil />
                      编辑
                    </Button>
                    <Button variant="ghost" size="sm" onClick={() => toggleStatus.mutate(asset)}>
                      {asset.status === "archived" ? "恢复" : "归档"}
                    </Button>
                    <span className="text-xs text-muted-foreground">#{asset.id}</span>
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
                  {asset.capabilities.length > 0 ? <span>能力：{asset.capabilities.join("、")}</span> : null}
                  {asset.task_key ? <span>能力标识：{asset.task_key}</span> : null}
                  {asset.solution_pack_id ? <span>方案：{asset.solution_pack_id}</span> : null}
                  {asset.training_task_id ? <span>训练任务：{asset.training_task_id}</span> : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
