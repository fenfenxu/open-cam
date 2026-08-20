import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, resolveApiUrl } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("api", () => {
  it("maps FastAPI string detail to ApiError.message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: "摄像头不存在" }),
      }),
    );

    try {
      await api("/api/cameras/9");
      throw new Error("should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(ApiError);
      expect((err as ApiError).message).toBe("摄像头不存在");
      expect((err as ApiError).status).toBe(404);
    }
  });

  it("joins FastAPI validation array details", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({
          detail: [{ loc: ["body", "name"], msg: "Field required", type: "missing" }],
        }),
      }),
    );

    await expect(api("/api/cameras")).rejects.toMatchObject({
      message: "Field required",
      status: 422,
    });
  });

  it("returns null on 204", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 204,
      }),
    );

    await expect(api("/api/events/1")).resolves.toBeNull();
  });

  it("console.errors on non-2xx", async () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        json: async () => ({ detail: "boom" }),
      }),
    );
    await expect(api("/api/config")).rejects.toBeInstanceOf(ApiError);
    expect(spy).toHaveBeenCalled();
    const args = spy.mock.calls[0].map(String).join(" ");
    expect(args).toContain("500");
    expect(args).toContain("/api/config");
  });

  it("keeps API paths same-origin when NEXT_PUBLIC_API_URL is set", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8600");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    });
    vi.stubGlobal("fetch", fetchMock);
    await api("/api/cameras");
    const [url] = fetchMock.mock.calls[0] as [string];
    expect(url).toBe("/api/cameras");
  });

  it("leaves /api paths relative even when NEXT_PUBLIC_API_URL is set", () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8600");
    expect(resolveApiUrl("/api/system/dev")).toBe("/api/system/dev");
    expect(resolveApiUrl("/api/cameras/1/live.mjpg")).toBe("/api/cameras/1/live.mjpg");
  });

  it("asks for JSON and skips HTTP cache for API requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => [],
    });
    vi.stubGlobal("fetch", fetchMock);
    await api("/api/cameras");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.cache).toBe("no-store");
    const headers = new Headers(init.headers);
    expect(headers.get("Accept")).toBe("application/json");
  });
});
