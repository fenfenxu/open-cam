import { describe, expect, it } from "vitest";
import { themeForSsr } from "./theme";

describe("themeForSsr", () => {
  it("ignores stored theme until mounted so SSR matches client hydration", () => {
    expect(themeForSsr("dark", false)).toBe("system");
    expect(themeForSsr("light", false)).toBe("system");
    expect(themeForSsr(undefined, false)).toBe("system");
  });

  it("uses the resolved theme after mount", () => {
    expect(themeForSsr("dark", true)).toBe("dark");
    expect(themeForSsr("light", true)).toBe("light");
    expect(themeForSsr(undefined, true)).toBe("system");
  });
});
