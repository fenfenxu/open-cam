import type { NextConfig } from "next";
import path from "node:path";

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8600";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  turbopack: {
    root: path.resolve(import.meta.dirname),
  },
};

if (process.env.NODE_ENV !== "production") {
  nextConfig.rewrites = async () => ({
    beforeFiles: [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/health", destination: `${API}/health` },
      { source: "/docs", destination: `${API}/docs` },
      { source: "/redoc", destination: `${API}/redoc` },
      { source: "/openapi.json", destination: `${API}/openapi.json` },
    ],
    // output:export 下未枚举的动态路径（/cameras/3、/marketplace/fast-food 等）
    // 在 dev 无法按 param 渲染；回退到 catch-all 首页，由客户端按真实路径路由，
    // 与 8600 单端口的 SPA fallback 行为对齐。
    fallback: [{ source: "/:path*", destination: "/" }],
  });
}

export default nextConfig;
