import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  oxc: {
    jsx: {
      runtime: "automatic",
    },
  },
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"] },
});
