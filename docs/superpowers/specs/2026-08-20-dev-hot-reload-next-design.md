# 开发体验：对齐 Multica 的前后端实践

日期：2026-08-20  
状态：已实现  

对照：`/Users/liuxu/repo/3part/multica`（Go + Next.js 16）。open-cam 后端保持 FastAPI（YOLO / 采集 / SQLite 不迁 Go）。

## 已拍板

- **前端开发工具改成 Next.js 16**（与 Multica `apps/web` 同一套）。放弃 Vite 作为控制台 bundler，从而直接使用 Next 自带的 Dev Overlay（左下角 `N` + Issues 胶囊、点开看 Console Error / 源码 / 堆栈）。不手写浮钮，不引入 `@visulima/vite-overlay`。
- **完整开发环境由 `make start` 统一启动**：FastAPI :8600 + `next dev` :5173。只启动后端用 `make backend`，浏览器开发态开 **5173**。
- **普通后端 `.py`**：`make start` 默认 uvicorn `--reload --reload-dir opencam`。
- **DDL 不跟文件监视走**：排除 `models.py` 与 `migrations/`；页面上确认后才触发一次进程替换，启动时 `ensure_schema` 执行迁移。
- **API 失败要进 overlay**：控制台 `api()` 在非 2xx 时 `console.error`，对标 Multica `packages/core/logger.ts` 把 `<- 500 /api/...` 打到 `console.error`，由 Next Overlay 收集。

## 非目标

- 把 Python 后端重写成 Go。
- 把全部 REST 一次性改挂到 `/api`（CLI / Skill 仍走现有路径；中期可另开）。
- 用 Next overlay 展示 Alembic / DDL（它只收集前端 console / 运行时错误）。
- 在 8600 的静态产物上使用 Next 内置 Dev Overlay（它只在 `next dev` 存在；8600 改走 `RuntimeOverlay`）。
- 一键代跑 `make revision`。

## 背景：Multica 实际用了什么

| 能力 | Multica | 不是 |
|---|---|---|
| 左下角 `N 3 Issues` 大弹窗 | Next.js 16 **内置** Dev Overlay（`next dev`） | 不是业务组件、不是 react-query-devtools |
| 弹窗里的 Console Error | 框架拦截 `console.error`；API 500 经 logger 打到 console | 不是自研 error UI 库 |
| 前端热更新 | `next dev` HMR；共享包导出源码 | — |
| 后端热加载 | **没有**。`go run ./cmd/server` | 无 air |
| DDL | 独立 `cmd/migrate`；`make start` **先** `migrate up` 再起服务 | 不跟文件监视绑定 |

Vite 自带 overlay 只管编译 / HMR，不拦截 `console.error`，也没有 Issues 胶囊。要对齐「页面上直接看到挂了什么」，必须换到 Next.js。

## 架构

```
浏览器 5173 ──页面 / HMR / Dev Overlay──► next dev (make start)
                 │
                 ├── NEXT_PUBLIC_API_URL → FastAPI :8600（撞名 REST：/cameras 等）
                 └── rewrite /api /docs /health /openapi.json → :8600

make start  uvicorn --reload --reload-dir opencam
            --reload-exclude models.py --reload-exclude 'migrations/*'
                 │
用户确认 DDL ── POST /api/system/dev/apply
                 └── 写入 opencam/_dev_reload.py（gitignore）
                     → 父进程换 worker → lifespan ensure_schema
```

### 前端：Vite → Next.js 16

`web/` 改为 Next App Router，对标 Multica `apps/web`：

| 项 | 决定 |
|---|---|
| Next | `^16`，`next dev` / `next build` |
| 页面 | 现有 `web/src/pages/*` 改为 `'use client'` 路由页（`app/cameras/page.tsx` 等），壳用现有 `AppShell` |
| 动态路由 | `app/cameras/[id]/page.tsx`、`app/training/[id]/page.tsx` |
| 去掉 | Vite、`@vitejs/plugin-react`、`react-router` 的浏览器路由（改 App Router） |
| 保留 | React 19、Tailwind v4、shadcn/Base UI、TanStack Query、vitest（组件单测仍 jsdom） |
| 生产单端口 | `next build` + `output: 'export'` 出静态文件；FastAPI 继续 SPA fallback。动态段在静态导出里用客户端 `useParams` 的页面必须能导出：相机/训练详情用客户端页 + 一条 `generateStaticParams` 返回空数组、`dynamicParams: true` **若 Next 16 export 不允许**，则生产改为导出一个 `app/[[...slug]]/page.tsx` 客户端壳、内部仍用现有页面组件按 `window.location.pathname` 渲染（保证 8600 单端口不挂 Node）。优先尝试按路由导出；建不过再退到 catch-all。 |
| 代理 | `next.config.ts` `rewrites()`：`/api/:path*`、`/docs`、`/redoc`、`/health`、`/openapi.json`、`/videos`、`/models` → `http://127.0.0.1:8600/...`（对标 Multica 把 `/api` rewrite 到 Go） |
| 撞名路径 | `/cameras`、`/events`、`/rules`、`/training` 既是页面也是 REST。Next 文件路由占页面；`api.ts` 对这些路径用 `NEXT_PUBLIC_API_URL`（默认 `http://127.0.0.1:8600`）直连 FastAPI。FastAPI 开发态允许 `5173` CORS。 |

`make start` = 后端 + `next dev --port 5173`。
`make serve` = `make ui-build` 后以无热更新的单端口后端运行。

### 后端热加载与 DDL

已有 `make start` / `stop` / `restart`、`devplaybook.py`。补齐：

1. `--reload-exclude`：`models.py`、`migrations/*`。改普通 `.py` 仍自动换进程。
2. `GET /api/system/dev`：`reload_on`、`state`（`idle` \| `need_revision` \| `need_apply`）、`title`、`detail`、`steps`、`can_apply`。检测复用 `classify` + `current_revision != head`。
3. `POST /api/system/dev/apply`：仅 `can_apply` 时写入 `opencam/_dev_reload.py` 触发 reload；否则 409。`RELOAD=0` 时 409，正文要求 `make restart`。
4. `need_revision`（只改了 `models.py`、没有迁移脚本）**禁止**确认重启，避免 `verify_schema` 拒启动。

DDL 确认 UI：控制台一条醒目横幅或 Dialog（shadcn），**不要**做成 Next Issues 胶囊的仿品。Next overlay 管前端报错；这条横幅管「该执行迁移了」。

状态：

| 状态 | 谁决定 | 用户看到 |
|---|---|---|
| 前端编译 / `console.error` | Next Dev Overlay | 左下角 Issues |
| 后端正在换进程 | `/health` 失败 | 横幅「正在加载…」；Overlay 里也会出现失败的 fetch `console.error` |
| 待 `make revision` | `need_revision` | 横幅，确认按钮禁用 |
| 待执行 DDL | `need_apply` | 横幅 +「确认并重启」 |
| 启动失败 | 确认后 60s `/health` 仍无 | 横幅「看终端」，可再确认或 `make restart` |

## 错误处理

- 确认重启会中断摄像头几秒，文案写明。
- 缺迁移脚本：接口拒绝，不杀进程。
- 迁移仍只在启动时 `ensure_schema`（备份 / 失败回滚），与升级安全文档一致。
- Next Overlay 只在 `next dev`（5173）存在。8600 静态包用左下角「报错」胶囊拦截同一批 `console.error`（`RuntimeOverlay`）。DDL 仍走顶部横幅，不是报错胶囊。

## 测试

- `devplaybook.classify`：ddl / migration / backend（已有则保持）。
- `GET/POST /api/system/dev`：`can_apply`、缺脚本 409、哨兵文件写入（测后删）。
- `api.ts`：非 2xx 调用了 `console.error`（可 mock）。
- 组件：DDL 横幅在 `need_apply` 显示确认按钮，`need_revision` 不显示。
- `tests/test_web.py`：随静态导出目录调整。
- 改 API 后 `make openapi`。

## 文档

`Makefile` help、`AGENTS.md`、`README.md`：本地开发 = `make start` + `make ui`，浏览器 **5173**。说明 Issues 胶囊来自 Next，不是产品功能。DDL 走横幅确认，不是 Issues。
