# AGENTS.md

本文件面向 AI 编码代理，介绍 open-cam 项目的架构、开发约定与常用命令。详细产品说明见 `README.md`。

## UI 任务必读

凡涉及 web/ 页面、组件、筛选、步骤、表格、抽屉、弹窗、状态、反馈、视觉或无障碍，**开始改代码前必须先读取仓库根目录 design.md**。design.md 是本项目 UI 组件选型和验收的执行基线：优先复用其中规定的公共组件，除非有充分的业务、技术或无障碍理由，不得在页面内重新选型；例外必须在交付说明中记录理由、影响范围和测试。完整说明见 docs/ui-design-system.md。

## 项目概览

open-cam 是视频监控分析工具：接入 RTSP 流或视频文件，YOLO + 规则引擎初筛事件，VLM（OpenAI 兼容接口）异步复核，事件入 SQLite，通过 Web 控制台 / REST API / `opencam` CLI / Agent Skill 消费。

- 语言：Python ≥ 3.12；构建后端 hatchling；包管理/运行用 **uv**。
- Web 框架：FastAPI + uvicorn（默认端口 8600）；数据库 SQLite + SQLAlchemy + Alembic 版本化迁移（pydantic-settings 管配置）。
- 检测：ultralytics YOLOv8 + ByteTrack（`lap` 是 ByteTrack 依赖）；OpenCV（headless 版）读流。
- Web 控制台：Next.js 16 + React + TypeScript + shadcn/ui（`web/`，Node 20+）。怎么开、改完怎么生效见「构建与运行命令」。5173 左下角 `N` 是 Next Dev Overlay；8600 静态包左下角「报错」胶囊收集同一批 `console.error`。改表结构走页面横幅确认，不是点 Issues。
- 前端、README、代码注释、规则名等用户可见内容均使用**中文**；代码标识符用英文。
- 事件分观察（`intent=observe`，如越线计数，只记录进客流统计）与待办（`needs_action=true`，需要人处置，有 open/acked/resolved/ignored 状态机）；`GET /api/stats/footfall` 只统计观察，`GET /api/stats/ops` 只统计待办。

## 运行时架构

```
RTSP/File ──► CaptureWorker(线程, 环形帧缓冲 deque)
                   │ 抽帧（detect_fps，默认 3）
                   ▼
            YOLO 检测 + ByteTrack 跟踪（device auto→cuda/mps/cpu）
                   │ 规则引擎命中（区域入侵/徘徊/人数统计/区域人数/越线计数）
                   ▼
            Event 落 SQLite + 快照存盘 ──► 异步队列 ──► VLM 复核回填判定
                   │                └──► 通知队列 ──► Notifier 推 webhook（留痕 EventAction）
                   ▼ 处置闭环：status(open/acked/resolved/ignored) + 星标/负责人/备注，全程记 EventAction
              FastAPI REST API ──► Web 控制台 / CLI / Skill / 监控 Agent
```

关键线程模型（`opencam/pipeline.py`、`opencam/streams/`、`opencam/detection/vlm.py`）：

- 每路运行中的摄像头一条 `CaptureWorker` 采集线程 + 一条 `PipelineWorker` 分析线程（daemon 线程）。
- `PipelineWorker._tick()`：取最新帧 → detect → 规则引擎 evaluate → 命中即存快照、事件落库、提交 VLM 队列。单帧异常不能让线程死（`noqa: BLE001` 兜底）。
- 所有 YOLO 推理经全局锁 `_INFERENCE_LOCK` 串行化（`detection/detector.py`）——多路并发调 `model.track` 在 MPS/Metal 上会段错误。
- VLM 复核是独立后台线程消费队列，绝不阻塞主链路；无 `OPENCAM_VLM_API_KEY` 时事件标 `vlm_status=skipped`。
- 通知推送（`opencam/notify.py` 的 `notifier`）同样是独立后台线程：按渠道的 camera_id/rule_type 通配匹配，逐渠道把成功/失败记入 `EventAction`；无渠道时直接丢弃。
- 服务启动（`main.py` lifespan）时初始化 DB、拉起 VLM 与 Notifier 线程，并恢复 DB 中 `status=running` 的摄像头。

## 代码组织

```
opencam/
├── main.py            FastAPI 应用入口、lifespan、路由挂载、/docs /redoc 定制、静态文件
├── config.py          Settings（pydantic BaseModel）：yaml + OPENCAM_* 环境变量覆盖；全局单例 settings
├── models.py          SQLAlchemy ORM（Camera/Video/Rule/Event/EventAction/NotifyChannel）+ Pydantic schema + 状态常量
├── db.py              engine/session 管理（init_db 触发版本化迁移 / get_session）
├── doctor.py          升级质检与健康检查（启动自检 verify_startup + check_health）
├── devplaybook.py     本地开发状态检查 / make dev-status / 启动横幅文案
├── migrations/        Alembic 版本化迁移：env.py + versions/ 版本脚本（规矩见「升级与数据安全」）
├── hardware.py        推理设备探测（cuda→mps→cpu）
├── pipeline.py        PipelineWorker/PipelineManager、start_camera/stop_camera
├── notify.py          Notifier 通知线程：事件匹配渠道推 webhook，结果留痕 EventAction
├── cli.py             opencam CLI（argparse + httpx，见下方约束）
├── api/               FastAPI 路由：cameras/videos/rules/rule_presets/events/notify/stats/system/packs/account/training/trained_models
├── detection/
│   ├── detector.py    YoloDetector / MockDetector / build_detector / Detection
│   ├── rules.py       RuleEngine：五种规则纯逻辑，可注入时钟便于单测
│   └── vlm.py         VlmReviewer 异步复核线程 + OpenAI 兼容调用
├── streams/           CaptureWorker 基类、FileSource（循环限速播放）、RTSPSource（指数退避重连）、manager
├── packs/             方案包：manifest 校验 / installer（目录/zip/URL 安装）/ apply（相对坐标→像素换算）
└── training/          自助训练：任务定义、抽帧、标注、本地微调评估、模型版本登记与 A/B 部署回滚

web/                   Next.js 16 控制台源码（构建产物 web/out，gitignore）
tests/                 pytest（见下）
agent/monitor_agent.py 示例监控 Agent：轮询未确认事件 → LLM 定级 → webhook → 自动 ack
packs/                 四个内置行业方案包（retail-chain/salon/restaurant/fast-food）
skills/opencam/        Agent Skill（拷到 ~/.agents/skills/ 使用）
scripts/export_openapi.py  导出 docs/openapi.json
scripts/dev_status.py      make dev-status：按 git 改动打印建议
docs/                  openapi.json 快照、upgrade-safety.md（升级与数据安全设计）、fastfood-analytics.md（需求全景）、cli-go-migration.md、model-training.md
rules/                 规则 yaml 示例
data/                  旧版默认数据目录（现已默认用户数据目录；本地 ./data 存在时首次启动自动搬迁）
```

## 构建与运行命令

命令以 `make help` 为准。Agent 先看生命周期，再按「改了什么」处理。

**怎么启动 / 重启**

| 目的 | 命令 | 浏览器 |
|------|------|--------|
| 完整开发环境（默认热加载） | `make start` | 前端 **5173**，后端 **8600/docs** |
| 无 YOLO 模型的完整开发环境 | `make start-mock` | 同上 |
| 只启动后端（高级/调试） | `make backend` | `8600/docs` |
| 停 / 重启（端口被占先 stop，不要再开一个进程） | `make stop` / `make restart` | — |
| 单端口运行（构建后端静态前端） | `make serve` | **8600** |

关热加载：`RELOAD=0 make start`。`PORT=xxxx` 可改端口。启动成功后终端会打一块横幅（热加载 / DDL / 前端 / schema）。

**改了什么 → 做什么**

| 改动 | 生效方式 |
|------|----------|
| `opencam/**/*.py`（不含表结构） | `make start` 已启动后端 reload，保存即换进程；未开 reload 则 `make restart` |
| `opencam/models.py` / DDL | **不会**因存盘而建列。`make revision m="说明"` → 人工 review → 控制台横幅确认重启（或 `make restart`）；启动时 `ensure_schema` 才跑迁移 |
| 已有迁移脚本 | 控制台横幅「确认并重启」，或 `make restart` |
| `web/src` | `make start` 已启动 Next HMR（5173）；单端口运行用 `make serve` |
| `opencam/api/`、`main.py` 路由 | 等 reload/restart 后 **`make openapi`** |
| 测试 | `make test` |

其余：`make install` / `install-dev` / `config`。CLI：`opencam cameras list`（开发时 `uv run opencam cameras list`）。

不确定改动如何生效时运行 `make dev-status`；它只是只读建议，不是启动命令。启动失败要读报错里的命令：缺列会提示 `make revision`；端口占用提示 `make stop`；控制台 503 会说明 `make start`（开发）或 `make serve`（单端口）。运行期质检：`GET /api/system/health` 或 `opencam system doctor`。

配置：可选 `config.yaml`（参考 `config.example.yaml`，已在 .gitignore）；任意字段可用 `OPENCAM_` + 大写字段名环境变量覆盖。VLM 的 api_key 可在控制台「设置 → 大模型」填写（写入本机 `data_dir/vlm.json`），也可用环境变量 `OPENCAM_VLM_API_KEY`（环境变量优先）。不要把 key 提交进仓库。

## 测试

```bash
uv run pytest        # 规则单测 + API 冒烟 + 端到端（mock detector，不下载模型）
```

约定：

- `tests/conftest.py` 的 `tmp_settings` 夹具把 `settings.data_dir` 指到 `tmp_path` 并强制 `OPENCAM_DETECTOR=mock`。**测试绝不触碰真实 YOLO 模型、不依赖网络。**
- 端到端测试（`test_pipeline_e2e.py`）用 OpenCV 生成合成视频（移动矩形）走完整链路。
- 规则引擎（`detection/rules.py`）是纯逻辑、时钟可注入，优先为规则变更补单测。
- 升级安全测试（`tests/test_upgrade.py`）：存量库接入、跨版本升级备份、失败回滚、质检 API、快照路径兼容。
- 当前基线：pytest 覆盖规则、API 冒烟、训练骨架、本地微调评估与模型版本部署/回滚；以 `make test` 为准（本文件不再手写个数）。

## 代码约定

- 模块顶部用中文 docstring 说明职责与关键设计决策；注释用中文。
- 所有文件 `from __future__ import annotations`。
- 状态用模块级常量（`CAMERA_RUNNING`、`VLM_PENDING` 等，见 `models.py`）。
- 后台线程一律 daemon + `threading.Event` 停止信号 + join 超时；循环内异常兜底记日志、不杀线程。
- DB 访问模式：`session = get_session()` → try/finally close；事件命中即落库（先 Event 后 VLM 异步回填）。
- 全局单例挂在模块级：`config.settings`、`streams.manager.camera_manager`、`pipeline.pipeline_manager`、`detection.vlm.vlm_reviewer`、`notify.notifier`。
- **CLI 约束**：`opencam/cli.py` 只能 import httpx/argparse 等轻量依赖，绝不能 import 会加载 ultralytics/torch 的包内模块（CLI 必须秒起）。CLI 长期规划见 `docs/cli-go-migration.md`（对外分发时改用 Go 重写，现阶段保持 Python）。
- **无 linter/formatter 配置**：仓库未配置 ruff/black 等，不要擅自引入；跟随现有代码风格。
- 改动 API 后必须重跑 `uv run python scripts/export_openapi.py` 更新 `docs/openapi.json`。

## 升级与数据安全

产品在本机持续迭代，**升级不得清除或弄坏用户本地的库与快照**。设计细节与流程图见 `docs/upgrade-safety.md`，规矩如下：

- **数据与代码分离**：数据目录默认在用户级目录（`config.default_data_dir()`，macOS `~/Library/Application Support/open-cam`），不要把仓库内 `./data` 当发布默认值；旧版 `./data` 由 `config.migrate_legacy_data_dir()` 在首次启动时自动复制过去（不删旧目录）。
- **schema 变更一律走 Alembic**：改 `models.py` 表结构后跑 `make revision m="说明"` 生成版本脚本，人工 review 后入库；禁止在运行期手写 ALTER 补丁（`migrations._legacy_fixup` 只服务 Alembic 引入前的存量库，不再扩展）。
- **版本脚本要幂等**：upgrade 前用 `sa.inspect` 判存在性（参照 `0002`/`0003`/`0004`），因为存量库可能已被旧补丁补过部分结构。
- **破坏性变更分两步走**：先加新列/新表 + 双写/回填，隔一个版本再删旧的；绝不在一个版本里又改结构又毁旧数据。
- **迁移前备份、失败回滚**：`init_db` → `migrations.ensure_schema` 在版本变化前自动把库文件复制到 `data_dir/backups/`，升级后跑 `verify_schema` 质检，不合格自动还原。不要绕过它直接改 schema。
- **升级质检**：启动时 lifespan 跑 `doctor.verify_startup()`，不合格拒绝启动（fail fast）；`verify_schema` 检查缺表**和缺列**（只改 `models.py` 没写迁移会在启动时报 `make revision`）。运行期用 `GET /api/system/health` 或 `opencam system doctor`。
- **快照路径只存相对 data_dir 的相对路径**（如 `snapshots/xxx.jpg`），读取统一走 `config.resolve_snapshot_path()`（兼容旧库的绝对路径，拒绝 `..` 穿越）。
- **测试**：迁移相关行为补 `tests/test_upgrade.py`（存量库接入、备份、回滚、质检均有样板）；发版前 `make test` 全绿。

## 安全注意事项

- 不要把帧/快照传到外部服务；出站调用仅限用户配置的 VLM 复核、方案包 URL 安装，以及通知 webhook（只发事件元数据，不含帧/快照）。
- 服务默认绑定由 uvicorn 命令行决定；README 示例用 127.0.0.1。
- 不要把 api key、token 写进仓库；控制台保存的 Key 落在本机数据目录的 `vlm.json` / `account.json`（已 gitignore）。环境变量仍可覆盖。
- 快照路径存 DB（`Event.snapshot_path`，相对 data_dir 的相对路径）；`GET /events/{id}/snapshot` 经 `resolve_snapshot_path` 解析并拒绝 `..` 穿越，改这里时保持这个约束。

## Agent 工作流不变量

本仓库的长程任务闭环由 loop-it 编排，实例事实见 `.loop-it/config.yaml`，
程序与巡检逻辑见 `~/.agents/skills/loop-it/SKILL.md`（不要把 skill 正文复制进本文件）。

- **指针原则**：项目内只放事实（`.loop-it/config.yaml`），程序以指针引用，不复制。
- **静默优先**：巡检一轮无动作则无产出，不为"看过了"建 issue 或刷评论。
- **唯一决策点**：验收与打回只对照计划文档（主 issue `plan_path`），不另立标准。
- **状态语义**：运行 issue 是日志不是交付物，本轮结束必须置 `done`/`blocked`，不留 `in_review`。
- **一个系统一个巡检器**：全 workspace 只允许一个通用 `Loop Patrol` autopilot，禁止为单个任务建 Autopilot。

## 常见任务提示

- 新增规则类型：改 `models.py` 的 `RuleCreate` pattern、`RULE_TYPE_NAMES`、`detection/rules.py` 的 evaluate 分支、`api/rule_presets.py` 预设元数据，并补 `tests/test_rules*.py` 单测。
- 新增 API 端点：在 `opencam/api/` 对应模块加路由，router 在 `main.py` 已挂载；同步补测试与 `docs/openapi.json`。
- 方案包格式（`packs/` 示例）：`pack.yaml`（id/name/version/vertical/... + presentation 产品文案 + 可选 cameras/experience/models）+ `rules/*.yaml`（多边形/线用 0-1 相对坐标，apply 时按摄像头分辨率换算）+ 可选 `prompts/*.txt`；`models[]` 声明随包模型，安装时登记为模型资产。fast-food 的 `experience/` 演示资产由 `scripts/gen_fastfood_previews.py` 生成、`scripts/check_pack_experience.py` 校验。演示/试跑源用纯白竖直人形 sprite 渲染，`MockDetector` 按亮度+竖直长宽比识别它们（内容驱动，无 sprite 时不产出检测）。
