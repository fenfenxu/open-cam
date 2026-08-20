export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function formatDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object" && "msg" in item) {
          return String((item as { msg: unknown }).msg);
        }
        return "";
      })
      .filter(Boolean);
    if (parts.length) return parts.join("; ");
  }
  return fallback;
}

export function resolveApiUrl(path: string): string {
  return path;
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers);
  if (!headers.has("Accept")) headers.set("Accept", "application/json");
  const resp = await fetch(resolveApiUrl(path), {
    ...options,
    cache: options?.cache ?? "no-store",
    headers,
  });
  if (!resp.ok) {
    let detail: unknown;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      detail = body.detail;
    } catch {
      detail = undefined;
    }
    const message = formatDetail(detail, `HTTP ${resp.status}`);
    console.error("[api]", `<- ${resp.status} ${path}`, { message });
    throw new ApiError(resp.status, message);
  }
  if (resp.status === 204) return null as T;
  return resp.json() as Promise<T>;
}

export function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
}
