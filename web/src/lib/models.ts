export type ModelSourceType =
  | "builtin"
  | "published"
  | "solution"
  | "uploaded"
  | "trained";

export type ModelKind =
  | "object_detection"
  | "classification"
  | "segmentation"
  | "pose"
  | "ocr"
  | "vlm";

export type ModelAsset = {
  id: number;
  name: string;
  description: string;
  source_type: ModelSourceType;
  model_kind: ModelKind;
  task_key: string | null;
  solution_pack_id: string | null;
  training_task_id: string | null;
  metadata: Record<string, unknown>;
  status: "active" | "archived";
  created_at: number;
  updated_at: number;
};

export const MODEL_SOURCE_TYPES: Array<{ value: ModelSourceType; label: string }> = [
  { value: "builtin", label: "系统内置模型" },
  { value: "published", label: "用户发布模型" },
  { value: "solution", label: "解决方案模型" },
  { value: "uploaded", label: "用户上传模型" },
  { value: "trained", label: "用户训练模型" },
];

export const MODEL_KINDS: Array<{ value: ModelKind; label: string }> = [
  { value: "object_detection", label: "目标检测" },
  { value: "classification", label: "图像分类" },
  { value: "segmentation", label: "图像分割" },
  { value: "pose", label: "姿态估计" },
  { value: "ocr", label: "文字识别" },
  { value: "vlm", label: "视觉大模型" },
];

export function modelSourceLabel(value: ModelSourceType): string {
  return MODEL_SOURCE_TYPES.find((item) => item.value === value)?.label || value;
}

export function modelKindLabel(value: ModelKind): string {
  return MODEL_KINDS.find((item) => item.value === value)?.label || value;
}
