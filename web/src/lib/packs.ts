// 规范化方案包模型：与后端 PackCard / PackDetail 一一对应。
// 前端只消费这些模型，不感知 manifest 新旧格式（旧包由后端规范化为
// 虚拟机位 + application.mode = "existing_camera"）。

export type PackAvailability = "available" | "unavailable" | "incompatible";
export type PackApplicationMode = "create_cameras" | "existing_camera";

export type PackCard = {
  id: string;
  name: string;
  version: string;
  vertical: string;
  author?: string;
  origin: string;
  fingerprint: string;
  tagline: string;
  description: string;
  availability: PackAvailability;
  unavailable_reason?: string | null;
  camera_count: number;
  rule_count: number;
  scene_count: number;
  has_demo: boolean;
  trial_available: boolean;
  application_mode: PackApplicationMode;
  cover_asset_id?: string | null;
};

export type PackOutcome = {
  title: string;
  description: string;
};

export type PackPresentation = {
  tagline: string;
  cover_asset_id?: string | null;
  outcomes: PackOutcome[];
  requirements: string[];
  limitations: string[];
};

export type PackCameraDetail = {
  id: string;
  name: string;
  purpose: string;
  placement: string;
  poster_asset_id?: string | null;
  rule_ids: string[];
};

export type PackRuleDetail = {
  id: string;
  name: string;
  type: string;
  type_label: string;
  camera_id?: string | null;
  cooldown: number;
  intent: string;
  summary: string;
};

export type PackSceneEvent = {
  at_sec: number;
  title: string;
  result: string;
  intent: string;
};

export type PackScene = {
  id: string;
  camera_id: string;
  title: string;
  available: boolean;
  degrade_reason?: string | null;
  input_asset_id?: string | null;
  result_asset_id?: string | null;
  poster_asset_id?: string | null;
  trial_available: boolean;
  events: PackSceneEvent[];
};

export type PackApplication = {
  mode: PackApplicationMode;
  camera_count: number;
  rule_count: number;
  auto_start: boolean;
  warnings: string[];
};

export type PackPrivacy = {
  processing: string;
  uploads_frames: boolean;
};

export type PackDetail = {
  id: string;
  name: string;
  version: string;
  vertical: string;
  author?: string;
  origin: string;
  fingerprint: string;
  description: string;
  availability: PackAvailability;
  unavailable_reason?: string | null;
  presentation: PackPresentation;
  cameras: PackCameraDetail[];
  rules: PackRuleDetail[];
  experience: { scenes: PackScene[] };
  application: PackApplication;
  privacy: PackPrivacy;
  readme_html: string;
  min_opencam_version: string;
  format_version: number;
};

export type PackApplyResult = {
  cameras?: { id: number; name?: string }[];
  rules?: { type: string; name?: string }[];
};

export const AVAILABILITY_NAMES: Record<PackAvailability, string> = {
  available: "可用",
  unavailable: "不可用",
  incompatible: "版本不兼容",
};

export const ORIGIN_NAMES: Record<string, string> = {
  builtin: "内置",
  installed: "已安装",
  online: "在线",
};

export function packAssetUrl(packId: string, assetId: string): string {
  return `/api/packs/${encodeURIComponent(packId)}/assets/${encodeURIComponent(assetId)}`;
}

export function packDetailPath(packId: string): string {
  return `/marketplace/${encodeURIComponent(packId)}`;
}

/** 场景是否有可在浏览器尝试播放的媒体（原始或结果）。 */
export function sceneHasMedia(scene: PackScene): boolean {
  return Boolean(scene.input_asset_id || scene.result_asset_id);
}
