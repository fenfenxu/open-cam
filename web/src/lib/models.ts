export type ModelOriginType = "builtin" | "uploaded" | "trained";

export type ModelDistributionType = "private" | "published" | "solution";

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
  origin_type: ModelOriginType;
  distribution_type: ModelDistributionType;
  model_kind: ModelKind;
  capabilities: string[];
  input_contract: Record<string, unknown>;
  output_contract: Record<string, unknown>;
  task_key: string | null;
  solution_pack_id: string | null;
  training_task_id: string | null;
  metadata: Record<string, unknown>;
  status: "active" | "archived";
  created_at: number;
  updated_at: number;
};

export const MODEL_ORIGIN_TYPES: Array<{ value: ModelOriginType; label: string }> = [
  { value: "builtin", label: "系统内置" },
  { value: "uploaded", label: "用户上传" },
  { value: "trained", label: "用户训练" },
];

export const MODEL_DISTRIBUTION_TYPES: Array<{ value: ModelDistributionType; label: string }> = [
  { value: "private", label: "仅本机使用" },
  { value: "published", label: "用户发布" },
  { value: "solution", label: "随方案交付" },
];

export const MODEL_KINDS: Array<{ value: ModelKind; label: string }> = [
  { value: "object_detection", label: "目标检测" },
  { value: "classification", label: "图像分类" },
  { value: "segmentation", label: "图像分割" },
  { value: "pose", label: "姿态估计" },
  { value: "ocr", label: "文字识别" },
  { value: "vlm", label: "视觉大模型" },
];

export function modelOriginLabel(value: ModelOriginType): string {
  return MODEL_ORIGIN_TYPES.find((item) => item.value === value)?.label || value;
}

export function modelDistributionLabel(value: ModelDistributionType): string {
  return MODEL_DISTRIBUTION_TYPES.find((item) => item.value === value)?.label || value;
}

export function modelKindLabel(value: ModelKind): string {
  return MODEL_KINDS.find((item) => item.value === value)?.label || value;
}
