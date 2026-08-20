export const VLM_PRESETS = [
  {
    id: "zhipu",
    name: "智谱 GLM（推荐，有免费档）",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    model: "glm-4v-flash",
  },
  {
    id: "qwen",
    name: "通义千问",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    model: "qwen-vl-plus",
  },
  {
    id: "kimi",
    name: "Kimi",
    base_url: "https://api.moonshot.cn/v1",
    model: "moonshot-v1-8k-vision-preview",
  },
  { id: "custom", name: "自定义 OpenAI 兼容接口", base_url: "", model: "" },
] as const;

export type SystemInfo = {
  version: string;
  device: string;
  device_config: string;
  memory_total_gb: number | null;
  vram_total_gb: number | null;
  detector: string;
  yolo_model: string;
  detect_fps: number;
  packs_available: number;
  packs_installed: number;
  data_dir?: string;
};

export type VlmConfig = {
  configured: boolean;
  base_url?: string;
  model?: string;
  api_key_hint?: string;
  api_key_source?: string;
  env_locked?: boolean;
};

export type AccountStatus = {
  platform_base_url?: string;
  logged_in: boolean;
  note?: string;
};

export type NotifyChannel = {
  id: number;
  name: string;
  webhook: string;
  camera_id: number | null;
  rule_type: string | null;
  enabled: boolean;
};

export type DevStatus = {
  reload_on: boolean;
  state: "idle" | "need_revision" | "need_apply";
  title: string;
  detail: string;
  steps: string[];
  can_apply: boolean;
};

export function matchVlmPreset(baseUrl: string | undefined): string {
  return VLM_PRESETS.find((p) => p.base_url && p.base_url === baseUrl)?.id ?? "custom";
}
