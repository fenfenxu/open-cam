# 控制台前端：用 shadcn/ui 替换原生 HTML/JS

日期：2026-08-19  
状态：待实现  
范围：把 `opencam/web/` 无构建原生 SPA 整栈换成 Vite + React + TypeScript + shadcn/ui（Base UI）。后端 FastAPI / REST 契约不变。

已拍板：

- 组件库选 **shadcn/ui**（2026 默认底层 **Base UI**），不要 Ant Design / Element Plus / Mantine。
- 浅色、深色、跟随系统都要；用户可手动切，默认跟随 OS。
- 表格、大屏、复杂表单、规则画布都在同一套视觉语言里，不按页面类型换库。

## 背景

当前控制台是 FastAPI 直接挂原生 JS：hash 路由、`innerHTML` 拼页、约 250 行自写 CSS。没有设计系统，新页面会从 `<select>` 再发明一遍，标准往下掉。对照过 Ant Design / Element Plus / Mantine / shadcn 的**真组件**后，选定 shadcn：视觉年代新、代码在仓库里可改、和 AI 工具链匹配。企业级表格、日期、表单校验不在 shadcn 本体里，用约定的配套库补，而不是再引一套中后台皮肤。

## 目标

1. 控制台所有现有页面用同一套 shadcn 组件与页面模板重写：仪表盘、摄像头列表/详情、规则、事件处置、训练、方案市场、设置。
2. 交互地板由模板锁死：列表页、详情抽屉、表单页、视频墙四种，新功能只许仿这四类。
3. 开发期 Vite 热更新；发布期打静态包，仍由 FastAPI 在本机提供，浏览器打开同一端口即用。
4. 用户可见文案继续中文；标识符英文。

## 非目标

- 改 REST API 形状、鉴权、把视频/快照传到外部。
- Next.js / Node 服务端渲染。本产品服务端是 FastAPI。
- 一次做完微前端、i18n 英文包、设计稿工具链。
- 把 CLI 打进前端构建（CLI 仍禁止 import 重依赖）。

## 技术栈

| 层 | 选型 | 原因 |
|---|---|---|
| 构建 | Vite 6 + React 19 + TypeScript | SPA，产物静态，适配 FastAPI |
| 组件 | shadcn/ui，底层 Base UI | 已选；2026 新项目默认 |
| 样式 | Tailwind CSS v4 + CSS 变量 | shadcn 的 token 层；主题切换零运行时 |
| 路由 | React Router（History） | 取代 `#/cameras/12`；深链可分享 |
| 服务端状态 | TanStack Query | 列表刷新、缓存、失败重试，替代手写 fetch+innerHTML |
| 表格 | TanStack Table + 自写 `DataTable` | 补 shadcn 没有的 ProTable |
| 表单 | react-hook-form + zod | 规则/摄像头/通知渠道校验 |
| 日期 | react-day-picker（shadcn calendar 块） | 事件筛选日期范围 |
| 图标 | lucide-react | shadcn 默认 |
| 主题 | `next-themes` 或等价（class=dark） | light / dark / system |
| 图表 | 先保留现有 canvas 客流图，需要时再上 Recharts | 不为仪表盘先加库 |

不引入 antd、element-plus、mantine、MUI。

## 仓库布局

前端独立目录，和 Python 包分开：

```
web/                          # Vite 应用（源码）
  package.json
  vite.config.ts
  src/
    main.tsx
    app/                      # 路由、QueryClient、主题
    components/ui/            # shadcn 生成，禁止手改行为除非必要
    components/app/           # 业务模板：AppShell、DataTable、PageHeader、DetailDrawer、VideoWall
    lib/api.ts                # fetch 封装，对应现有 /cameras /events …
    lib/utils.ts
    pages/                    # 一页一个路由模块
  dist/                       # 构建产物，gitignore
opencam/web/                  # 迁移完成后删除（现有原生 JS）
```

FastAPI：

- 开发：Vite `server.proxy` 把 `/cameras`、`/events`、`/api` 等打到 `http://127.0.0.1:8600`；控制台跑在 `:5173`。
- 发布：`npm run build` → `web/dist`；`main.py` 挂 `dist` 的静态资源，`GET /` 与非 API 路径回退 `index.html`（History 路由）。
- 必须把 API 路由注册放在静态挂载**之前**（现有已是如此），并增加 SPA fallback，避免刷新 `/events` 404。

Makefile 增加 `web-dev` / `web-build`；`make run` 在 `web/dist` 存在时提供完整控制台。README 写明控制台需要 Node 20+ 构建一次（开发用 `make web-dev` 可同时开后端）。

## 页面模板（质量地板）

后续功能只许落在这四类上，避免再发明一页一种密度。

1. **AppShell**：左侧导航 + 顶栏（面包屑、主题切换）+ `Outlet`。导航项与现网一致：仪表盘、摄像头、规则、事件、模型训练、方案市场、设置；外链 API 文档仍指向 `/docs`。
2. **DataTable 页**：顶栏筛选 + TanStack 表 + 分页 + 行操作；点行打开 **DetailDrawer**（事件快照、处置、时间线）。事件处置必须用这个，不许再做「下面再塞一块详情」。
3. **Form 页**：分区、校验、主按钮/次按钮、危险操作二次确认。摄像头接入、规则参数、通知渠道、设置走这里。
4. **VideoWall**：多画面网格 + 可选右侧事件条。仪表盘与摄像头详情的直播/回放走这里；MJPEG 仍用现有 `GET /cameras/{id}/live.mjpg`，文件回放仍用 `/source`。规则多边形继续画在画面上，用独立 canvas 叠层，不换检测协议。

三种主题都套同一模板，只换 token，不换布局。

## 现有页面映射

| 现页 | 新路由 | 模板 |
|---|---|---|
| 仪表盘 | `/` | VideoWall（卡片网格 + 客流条） |
| 摄像头列表 | `/cameras` | DataTable + 新建用 Form/Dialog |
| 摄像头详情 | `/cameras/:id` | VideoWall + 侧栏规则/事件 |
| 规则 | `/rules` 或挂在摄像头下 | Form + 画布 |
| 事件处置 | `/events` | DataTable + DetailDrawer |
| 模型训练 | `/training`、`/training/:id` | Form 向导 + DataTable |
| 方案市场 | `/marketplace` | 卡片网格（仍是 AppShell 内） |
| 设置 | `/settings` | Form |

行为与文案对齐现网：摄像头已创建只能改名；RTSP 不支持回放提示；事件状态流转、星标、指派、备注、重发通知；训练向导与模型版本。不借机改产品规则。

## 迁移策略

一次切控制台，不双栈并存上线。

1. 搭 `web/` 脚手架 + AppShell + 主题 + `lib/api.ts` 打通一个 `GET /cameras`。
2. 先做 **事件处置**（表格+抽屉最能定地板），再摄像头列表/详情，再规则画布，再仪表盘，其余页按依赖跟上。
3. 全部页面可点通后：FastAPI 改挂 `web/dist`，删 `opencam/web/` 原生文件，改 `tests/test_web.py`（不再断言 `app.js` 字符串；改为首页 HTML 含构建产物、关键路由可加载）。
4. 前端单测用 Vitest 测 DataTable/主题/api 错误处理；E2E 本期不做 Playwright，除非事件抽屉交互回归不够稳。

中间态允许仓库里同时有 `opencam/web` 与 `web/`，但只服务一套。切换开关是 `main.py` 的静态目录，不是用户看到两个控制台。

## 约束（从产品不变式抄过来）

- 视频与快照不出本机；前端只请求本机 API 与本地 blob。
- 快照 URL 继续走现有 `GET /events/{id}/snapshot`，不自己拼 `data_dir`。
- 用户可见中文；代码标识符英文。
- 不把 api key 写进前端源码或 `config.yaml`。
- 构建产物不入库；CI 先 `npm ci && npm run build` 再 `make test`。

## 风险

- shadcn 表格要自建：必须先落地 `components/app/data-table.tsx`，否则事件页会退回裸 `<table>`。
- History 路由刷新 404：FastAPI 漏写 SPA fallback 会在第一次分享链接时爆。
- 规则画布是自定义，和组件库无关；工时不要估进「换 shadcn」。
- 无构建 → 有构建：Python-only 贡献者要装 Node；Makefile 必须一条命令能起。

## 验收

- 本机 `make web-dev` 与 `make run`（已 build）都能打开控制台，主题三态可切。
- 现有页面功能不丢（对照上表），事件筛选/处置、摄像头启停、直播/回放文案仍对。
- `make test` 全绿；不再依赖 `/static/app.js`。
- 仓库无 `opencam/web/pages/*.js` 与对照原型。
