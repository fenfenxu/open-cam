import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
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
      await api("/cameras/9");
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

    await expect(api("/cameras")).rejects.toMatchObject({
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

    await expect(api("/events/1")).resolves.toBeNull();
  });
});
