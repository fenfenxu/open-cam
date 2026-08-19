export type Camera = {
  id: number;
  name: string;
  source_type: string;
  source_uri: string;
  status: string;
};

export type VideoAsset = {
  id: number;
  filename: string;
  path: string;
  size_bytes: number;
  duration_sec: number | null;
  width: number | null;
  height: number | null;
};

export type ModelVersion = {
  id: number;
  task_id: string;
  slot_key: string;
  status: string;
};

export const SOURCE_TYPE_NAMES: Record<string, string> = {
  file: "视频文件",
  rtsp: "RTSP 流",
};

export function jsonBody(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export function dash(value: string | number | null | undefined): string {
  return value == null || value === "" ? "—" : String(value);
}
