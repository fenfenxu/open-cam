import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const API_TARGET = "http://127.0.0.1:8600";

const proxyPrefixes = [
  "/cameras",
  "/videos",
  "/events",
  "/training",
  "/models",
  "/health",
  "/docs",
  "/redoc",
  "/openapi.json",
  "/api",
];

export default defineConfig({
  base: "/",
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "./src"),
    },
  },
  server: {
    proxy: Object.fromEntries(
      proxyPrefixes.map((prefix) => [
        prefix,
        {
          target: API_TARGET,
          changeOrigin: true,
          // History 路由与 REST 同路径：浏览器打开 /cameras 要 SPA，fetch 才走 API
          bypass(req) {
            const accept = req.headers.accept ?? "";
            if (req.method === "GET" && accept.includes("text/html")) {
              return "/index.html";
            }
          },
        },
      ]),
    ),
  },
  test: {
    environment: "jsdom",
  },
});
