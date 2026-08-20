"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BrainCircuit, Plus } from "lucide-react";
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
  MODEL_KINDS,
  MODEL_SOURCE_TYPES,
  modelKindLabel,
  modelSourceLabel,
  type ModelAsset,
  type ModelKind,
  type ModelSourceType,
} from "@/lib/models";

function errorMessage(error: unknown): string {
  return error instanceof ApiError || error instanceof Error ? error.message : "请求失败";
}

export function ModelsPage() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sourceType, setSourceType] = useState<ModelSourceType>("uploaded");
  const [modelKind, setModelKind] = useState<ModelKind>("object_detection");
  const [taskKey, setTaskKey] = useState("");
  const [solutionPackId, setSolutionPackId] = useState("");

  const assetsQuery = useQuery({
    queryKey: ["model-assets"],
    queryFn: () => api<ModelAsset[]>("/api/models/assets"),
  });

  const createAsset = useMutation({
    mutationFn: () => api<ModelAsset>("/api/models/assets", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name: name.trim(),
        description: description.trim(),
        source_type: sourceType,
        model_kind: modelKind,
        task_key: taskKey.trim() || null,
        solution_pack_id: solutionPackId.trim() || null,
      }),
    }),
    onSuccess: async () => {
      toast.success("模型资产已登记");
      setName("");
      setDescription("");
      setTaskKey("");
      setSolutionPackId("");
      await queryClient.invalidateQueries({ queryKey: ["model-assets"] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const assets = assetsQuery.data ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader
        title="模型管理"
        description="登记模型名称、描述、来源和能力。模型资产与规则关联独立管理。"
      />

      <section className="rounded-xl border bg-card p-5 shadow-sm">
        <div className="mb-4 flex items-center gap-2">
          <Plus className="size-4" />
          <h2 className="font-medium">登记模型资产</h2>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="model-name">模型名称</Label>
            <Input id="model-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：门店人员检测模型" />
          </div>
          <div className="space-y-2">
            <Label>模型来源</Label>
            <Select value={sourceType} onValueChange={(value) => value && setSourceType(value as ModelSourceType)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>{MODEL_SOURCE_TYPES.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label>模型类型</Label>
            <Select value={modelKind} onValueChange={(value) => value && setModelKind(value as ModelKind)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>{MODEL_KINDS.map((item) => <SelectItem key={item.value} value={item.value}>{item.label}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="model-task-key">能力标识（可选）</Label>
            <Input id="model-task-key" value={taskKey} onChange={(event) => setTaskKey(event.target.value)} placeholder="例如：person_detection" />
          </div>
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="model-description">模型描述</Label>
            <Textarea id="model-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="描述适用场景、识别目标、限制条件等，后续可用于 AI 推荐关联。" />
          </div>
          {sourceType === "solution" ? (
            <div className="space-y-2">
              <Label htmlFor="solution-pack-id">方案标识（可选）</Label>
              <Input id="solution-pack-id" value={solutionPackId} onChange={(event) => setSolutionPackId(event.target.value)} placeholder="例如：fast-food" />
            </div>
          ) : null}
        </div>
        <div className="mt-4 flex justify-end">
          <Button disabled={!name.trim() || createAsset.isPending} onClick={() => createAsset.mutate()}>
            <BrainCircuit />
            登记模型
          </Button>
        </div>
      </section>

      <section className="rounded-xl border bg-card p-5 shadow-sm">
        <h2 className="mb-4 font-medium">已登记模型（{assets.length}）</h2>
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
                      <Badge variant="secondary">{modelSourceLabel(asset.source_type)}</Badge>
                      <Badge variant="outline">{modelKindLabel(asset.model_kind)}</Badge>
                    </div>
                    <p className="text-sm text-muted-foreground">{asset.description || "暂无描述"}</p>
                  </div>
                  <span className="text-xs text-muted-foreground">#{asset.id}</span>
                </div>
                <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
                  {asset.task_key ? <span>能力：{asset.task_key}</span> : null}
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
