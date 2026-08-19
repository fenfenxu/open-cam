# shadcn 控制台替换 Implementation Plan

> **For agentic workers:** 验收只对照本计划与规格 `docs/superpowers/specs/2026-08-19-shadcn-console-design.md`。REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans。从 `origin/main` 开分支。PR 标题和正文必须含对应 Multica issue key（如 `CAM-xx` / `Closes CAM-xx`）。

**Goal:** 用 Vite + React + TypeScript + shadcn/ui 整栈替换 `opencam/web/` 原生 HTML/JS 控制台，现有 REST 行为与中文文案不丢。

**Architecture:** 仓库根新增 `web/` SPA。开发期 Vite `:5173` 把现有 REST 路径代理到 uvicorn `:8600`。发布期 `web/dist` 由 FastAPI 挂静态文件，History 路由靠 SPA fallback。质量地板是四块模板（AppShell / DataTable / DetailDrawer / Form / VideoWall），页面只许仿这几类。后端 API 形状不变。

**Tech Stack:** Vite 6、React 19、TypeScript、shadcn/ui（Base UI）、Tailwind CSS v4、React Router、TanStack Query、TanStack Table、react-hook-form、zod、react-day-picker、lucide-react、class `dark` 主题（light / dark / system）。禁止 antd / element-plus / mantine / MUI。

## Global Constraints

- 规格原文：`docs/superpowers/specs/2026-08-19-shadcn-console-design.md`（路由表、四模板、非目标以规格为准）。
- 基线：`origin/main`。不要把工作区里与本需求无关的未提交改动卷进本 PR。
- 用户可见文案中文；标识符英文；`from __future__ import annotations` 仅适用于 Python 改动。
- **禁止**改 REST schema、鉴权、把帧/快照传到外部、把 api key 写进前端。
- **禁止**引入 Ant Design / Element Plus / Mantine / MUI。
- 快照只走 `GET /events/{id}/snapshot`。直播 `GET /cameras/{id}/live.mjpg`，文件回放 `GET /cameras/{id}/source`。
- 测试：Python 侧 `tmp_settings` + `OPENCAM_DETECTOR=mock`；前端 Vitest 不依赖真实 YOLO / 网络。
- 构建产物 `web/dist`、`web/node_modules` 不入库。
- CLI 不得 import 本前端，也不得为了控制台去加载 torch。

## 范围边界

做：`web/` 脚手架、四模板、规格中的全部现网页面、主题三态、FastAPI 改挂 dist + SPA fallback、删除 `opencam/web/` 原生文件、改 `tests/test_web.py` / Makefile / README / AGENTS.md。

不做：Next.js、Playwright E2E、英文 i18n、改事件/摄像头 API、规则引擎、VLM、通知后端。

## 验收标准（DoD）

1. `make web-dev` 能开控制台（Vite 代理到已启动的 uvicorn）；`npm run build` 后 `make run` 也能开同一套控制台。
2. 主题：浅色 / 深色 / 跟随系统，可手动切，刷新不丢。
3. 页面与现网功能对齐（见下表），路由为 History：`/`、`/cameras`、`/cameras/:id`、`/events`、`/training`、`/training/:id`、`/marketplace`、`/settings`；规则可在摄像头详情或 `/rules` 进入，与现网能力等价即可。
4. 事件页：筛选（摄像头/类型/状态/判定/仅关注）+ DataTable + 行开 DetailDrawer（快照、状态流转、星标、指派、备注、时间线、重发通知、训练反馈）；禁止「表格下面再塞一块详情」。
5. 摄像头：列表可建/启停/改名；已创建不能改类型和源（文案含「请新建」）；详情运行中显示 MJPEG；file 可回放；rtsp 文案 `该源为直播流，不支持回放`。
6. `make test` 全绿；`tests/test_web.py` 不再断言 `/static/app.js`；仓库无 `opencam/web/pages/*.js`。
7. 刷新 `/events` 不 404（SPA fallback）。

## 拆解思路（子 Issue / stage）

| Stage | 子任务 | 可独立验收 |
|---|---|---|
| 1 | 脚手架 + AppShell + 主题 + api 客户端 | `web/` 能 `npm run dev`；壳能切主题；`GET /cameras` 经 proxy 成功 |
| 2 | DataTable / DetailDrawer + 事件处置 | 事件筛选/抽屉/处置闭环可点；Vitest 覆盖 api 错误与表格空态 |
| 3 | 摄像头列表 + 详情（VideoWall/直播/回放） | 列表 CRUD 约束 + 详情 live.mjpg / source 文案 |
| 4 | 规则表单 + 画布 | 能建五类规则（区域点选），冷却/类别可填 |
| 5 | 仪表盘 | 卡片网格 + 运行中快照/MJPEG + 24h 客流条 |
| 6 | 训练 / 方案市场 / 设置 | 与现网 API 对齐的可走通页面 |
| 7 | FastAPI 切 dist、删原生 web、测与文档 | `make test` 全绿；无 `opencam/web/pages`；README/AGENTS 写构建 |

后一 stage 基于前一 stage 已合入（或同一分支续做）。Stage 7 之前 `main.py` 仍挂旧 `opencam/web/`，避免半成品控制台进默认 `make run`。

## 文件地图

- Create: `web/`（Vite 应用全文）
- Modify: `opencam/main.py`（仅 Stage 7：静态目录 + SPA fallback）
- Modify: `Makefile`、`README.md`、`AGENTS.md`、`.gitignore`
- Modify: `tests/test_web.py`
- Delete（Stage 7）: `opencam/web/index.html`、`app.js`、`style.css`、`pages/*.js`
- 不改：`opencam/api/*`、`docs/openapi.json`、`opencam/cli.py`

## 共享接口（后一 stage 只依赖这些名字）

`web/src/lib/api.ts`：

```ts
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T>;
export function fmtTime(ts: number): string; // Unix 秒 → zh-CN 24h
```

`web/src/lib/labels.ts`：

```ts
export const RULE_TYPE_NAMES: Record<string, string>; // zone_intrusion… 与现网一致
export const STATUS_NAMES: Record<string, string>; // open 待处理 / acked 已确认 / resolved 已处置 / ignored 已忽略
export const NEXT_ACTIONS: Record<string, [string, string][]>; // 与 opencam/web/pages/events.js 相同
```

Vite proxy 前缀（不要加 `/api` 总前缀，现网就是根路径）：

`/cameras` `/videos` `/events` `/training` `/models` `/health` `/docs` `/redoc` `/openapi.json` `/api`

---

### Task 1: 脚手架 + AppShell + 主题 + api

**Files:**
- Create: `web/package.json`、`web/vite.config.ts`、`web/tsconfig.json`、`web/index.html`、`web/src/main.tsx`、`web/src/app/providers.tsx`、`web/src/app/router.tsx`、`web/src/components/app/app-shell.tsx`、`web/src/lib/api.ts`、`web/src/lib/utils.ts`、`web/src/pages/placeholder.tsx`、`web/src/index.css`
- Modify: `.gitignore`（加 `web/node_modules/`、`web/dist/`）、`Makefile`（`web-dev` / `web-build`）
- Test: `web/src/lib/api.test.ts`（Vitest，mock fetch）

**Interfaces:**
- Consumes: 无
- Produces: `api<T>()`、`ApiError`、`AppShell`、主题 `ThemeProvider`（`attribute="class"`，default `system`）、路由骨架、`make web-dev`

- [ ] **Step 1:** `cd web && npm create vite@latest . -- --template react-ts` 后按 shadcn 当前文档初始化（Base UI 默认）。加入依赖：`react-router`、`@tanstack/react-query`、`next-themes`（或 shadcn 主题方案）、`class-variance-authority`、`clsx`、`tailwind-merge`。`npx shadcn@latest add button dropdown-menu sonner`（最少壳要用的）。

- [ ] **Step 2:** `vite.config.ts` 的 `server.proxy` 把上表前缀转到 `http://127.0.0.1:8600`。`base: '/'`。

- [ ] **Step 3:** 实现 `api()`：`fetch(path)`，非 2xx 读 `detail`（字符串或 FastAPI 校验数组）丢 `ApiError`；204 返回 `null`。Vitest：404 body `{detail:"摄像头不存在"}` 时 `err.message === "摄像头不存在"`。

- [ ] **Step 4:** `AppShell` 左导航中文项与现网一致（仪表盘 `/`、摄像头 `/cameras`、事件 `/events`、训练 `/training`、方案市场 `/marketplace`、设置 `/settings`；规则可先链到 `/cameras`）。顶栏主题三项。外链 `/docs` `target=_blank`。底栏保留「本地运行 · 数据不出本机」。

- [ ] **Step 5:** `Makefile`：

```makefile
web-dev: ## 启动 Vite 控制台（需另开 make run）
	cd web && npm run dev

web-build: ## 构建 web/dist
	cd web && npm ci && npm run build
```

- [ ] **Step 6:** 浏览器打开 Vite，切换主题三态；占位页调用 `api('/cameras')` 能列出（后端需在跑）。Commit。

---

### Task 2: DataTable / DetailDrawer + 事件处置

**Files:**
- Create: `web/src/components/app/data-table.tsx`、`web/src/components/app/detail-drawer.tsx`、`web/src/components/app/page-header.tsx`、`web/src/lib/labels.ts`、`web/src/pages/events.tsx`
- Test: `web/src/components/app/data-table.test.tsx`（空数据渲染「暂无事件」）

**Interfaces:**
- Consumes: `api`、`fmtTime`、`RULE_TYPE_NAMES`、`STATUS_NAMES`、`NEXT_ACTIONS`
- Produces: `DataTable<T>`（columns + data + onRowClick）、`DetailDrawer`（open / onOpenChange / title / children）、事件页路由 `/events`

行为必须对齐 `opencam/web/pages/events.js`：

- 筛选：`camera_id`、`rule_type`、`status`、`vlm_verdict`、`starred=true`、`limit=100`
- 行点击打开抽屉，不要页内下方详情块
- 抽屉：快照 `<img src={/events/{id}/snapshot}>`、PATCH 字段、`GET /events/{id}/actions` 时间线、`POST /events/{id}/notify`、`POST /events/{id}/feedback`
- `NEXT_ACTIONS` 与现文件逐字相同

- [ ] **Step 1:** 用 shadcn `table` `sheet` `select` `badge` `checkbox` `button` 组装模板；表格底层 TanStack Table。
- [ ] **Step 2:** 实现事件页。失败 toast（sonner）显示 `ApiError.message`。
- [ ] **Step 3:** Vitest 空态。手动点筛选与抽屉。Commit。

---

### Task 3: 摄像头列表 + 详情

**Files:**
- Create: `web/src/pages/cameras.tsx`、`web/src/pages/camera-detail.tsx`、`web/src/components/app/video-wall.tsx`
- Test: 无新 pytest；对照现网文案做页面断言（Vitest 渲染「请新建」「该源为直播流，不支持回放」）

**Interfaces:**
- Consumes: `api`、`DataTable`、`VideoWall`
- Produces: `/cameras`、`/cameras/:id`

对齐 `cameras.js` / `camera.js`：

- 新建：name、source_type file|rtsp、source_uri、可选上传 `POST /cameras/upload`、autostart
- 行内改名 `PUT`；禁止改已创建的 type/uri；删除/启停按钮
- 已上传视频表 `GET /videos`
- 详情：`status=running` 时 `<img src={/cameras/{id}/live.mjpg}>`；file 用 `<video src={/cameras/{id}/source} controls>`；rtsp 显示 `该源为直播流，不支持回放`

- [ ] **Step 1:** 列表 + 新建 Dialog（Form + zod：name 必填，uri 必填）。
- [ ] **Step 2:** `VideoWall`：单格直播/回放；详情页用一格主画面。
- [ ] **Step 3:** 文案测试。Commit。

---

### Task 4: 规则表单 + 画布

**Files:**
- Create: `web/src/pages/rules.tsx` 或详情页内规则区 `web/src/components/app/rule-canvas.tsx`
- 对照：`opencam/web/pages/rules.js`（三步：类型 → 画区域 → 参数）

**Interfaces:**
- Consumes: `api`、摄像头 id、快照或 live 作底图
- Produces: `POST /cameras/{id}/rules`，params 与现网相同（polygon / line / classes / cooldown）

- [ ] **Step 1:** 五类规则中文名用 `RULE_TYPE_NAMES`；canvas 叠在画面上点选多边形/线，坐标存像素（与现网一致，不要改成另一套协议）。
- [ ] **Step 2:** 列表可删规则。Commit。

---

### Task 5: 仪表盘

**Files:**
- Create: `web/src/pages/dashboard.tsx`
- 对照：`opencam/web/pages/dashboard.js`（卡片、1fps 快照轮询、canvas 客流图）

**Interfaces:**
- Consumes: `api('/cameras')`、`/events?camera_id=`、`/api/stats/footfall?camera_id=`
- Produces: `/` 仪表盘；卡片点击进 `/cameras/:id`

- [ ] **Step 1:** VideoWall/卡片网格；无摄像头时空态「还没有摄像头，去「摄像头」页添加一路。」
- [ ] **Step 2:** 客流双色柱用 canvas，图例「进/出」；无数据提示配置越线计数。Commit。

---

### Task 6: 训练 / 方案市场 / 设置

**Files:**
- Create: `web/src/pages/training.tsx`、`web/src/pages/marketplace.tsx`、`web/src/pages/settings.tsx`
- 对照：`training.js`、`marketplace.js`、`settings.js` 的 API 路径与向导步骤，功能等价，UI 走 Form + DataTable，不发明第三种密度。

**Interfaces:**
- 设置：`GET /api/system/info`、`GET /api/account/status`、通知渠道 CRUD（与现网 `/api/notify` 或 settings.js 实际路径一致，以现文件为准）
- 市场：`/api/packs`、`/api/packs/online`、install/apply/DELETE
- 训练：`/training/tasks` 向导与 `/models` 部署回滚

- [ ] **Step 1:** 三页都接到 AppShell 导航，空态/错误用同样 toast。
- [ ] **Step 2:** 训练向导关键文案保留「说需求」。Commit。

---

### Task 7: 切 FastAPI、删原生 web、测试与文档

**Files:**
- Modify: `opencam/main.py`（`WEB_DIR` 改为 `Path(__file__).resolve().parents[1] / "web" / "dist"`；静态挂载在 API 之后；增加 SPA fallback）
- Modify: `tests/test_web.py`（断言 dist `index.html` 含 `open-cam`；**删除**对 `app.js` / `pages/*.js` 的字符串探针；改为构建产物存在且 `/events` 回退 HTML）
- Modify: `README.md`、`AGENTS.md`（控制台改为有构建步骤；开发 `make web-dev`）
- Delete: `opencam/web/` 全部原生文件
- Test: `make web-build && OPENCAM_DETECTOR=mock uv run pytest tests/test_web.py tests/test_cameras_api.py -q` 再 `make test`

**SPA fallback（挂在所有 router 之后）：**

```python
DIST = Path(__file__).resolve().parents[1] / "web" / "dist"

@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    candidate = (DIST / full_path).resolve()
    if DIST.resolve() in candidate.parents or candidate == DIST.resolve():
        if candidate.is_file():
            return FileResponse(candidate)
    return FileResponse(DIST / "index.html")
```

禁止 `..` 逃出 dist。API 路由必须先注册，此函数最后。

- [ ] **Step 1:** 先 `make web-build` 再改 `main.py`，确认 `/` 与刷新 `/events` 为 200 HTML。
- [ ] **Step 2:** 重写 `test_web.py`。无 dist 时测试 skip 或 fixture 指向已构建目录——CI/本地约定：跑 web 测试前必须 build。
- [ ] **Step 3:** 删 `opencam/web/`。更新 README/AGENTS。`make test` 全绿。Commit。

## 风险（执行时不要踩）

- Stage 7 之前不要改 `main.py` 默认静态目录。
- DataTable 必须在事件页之前落地，禁止事件页手写 `<table>`。
- Vite proxy 漏了 `/api` 会导致设置/市场/客流全挂。
- 规则画布工时独立，不要算进「装 shadcn」。
