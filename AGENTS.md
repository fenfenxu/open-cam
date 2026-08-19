# AGENTS.md

本文件面向 AI 编码代理，介绍 open-cam 项目的架构、开发约定与常用命令。详细产品说明见 `README.md`。

## 项目概览

open-cam 是视频监控分析工具：接入 RTSP 流或视频文件，YOLO + 规则引擎初筛事件，VLM（OpenAI 兼容接口）异步复核，事件入 SQLite，通过 Web 控制台 / REST API / `opencam` CLI / Agent Skill 消费。

- 语言：Python ≥ 3.12；构建后端 hatchling；包管理/运行用 **uv**。
- Web 框架：FastAPI + uvicorn（默认端口 8600）；数据库 SQLite + SQLAlchemy + Alembic 版本化迁移（pydantic-settings 管配置）。
- 检测：ultralytics YOLOv8 + ByteTrack（`lap` 是 ByteTrack 依赖）；OpenCV（headless 版）读流。
- Web 控制台：Vite + React + TypeScript + shadcn/ui（`web/`）。开发用 `make web-dev`（代理到已启动的后端）；`make web-build` 后 FastAPI 挂 `web/dist`，`make run` 提供同一套控制台。需要 Node 20+。
- 前端、README、代码注释、规则名等用户可见内容均使用**中文**；代码标识符用英文。

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

web/                   Vite 控制台源码（构建产物 web/dist，gitignore）
tests/                 pytest（见下）
agent/monitor_agent.py 示例监控 Agent：轮询未确认事件 → LLM 定级 → webhook → 自动 ack
packs/                 四个内置行业方案包（retail-chain/salon/restaurant/fast-food）
skills/opencam/        Agent Skill（拷到 ~/.agents/skills/ 使用）
scripts/export_openapi.py  导出 docs/openapi.json
docs/                  openapi.json 快照、upgrade-safety.md（升级与数据安全设计）、fastfood-analytics.md（需求全景）、cli-go-migration.md、model-training.md
rules/                 规则 yaml 示例
data/                  旧版默认数据目录（现已默认用户数据目录；本地 ./data 存在时首次启动自动搬迁）
```

## 构建与运行命令

```bash
# 安装（必须 uv）
uv venv --python 3.12
uv pip install -e .

# 启动服务（默认端口 8600；控制台需先 make web-build）
uv run uvicorn opencam.main:app --port 8600
make web-dev     # 开发控制台（另开终端，代理到 8600）
make web-build   # 产出 web/dist，随后 make run 即可打开控制台

# 无模型环境/CI（不下载 yolov8n.pt）
export OPENCAM_DETECTOR=mock

# CLI（安装后）
opencam cameras list        # 或开发时 uv run opencam cameras list

# 改动 API 后重新导出 schema 快照
uv run python scripts/export_openapi.py
```

上述命令在根目录 `Makefile` 中有对应 target（`make install / run / run-mock / test / web-dev / web-build / openapi / config / clean`，`make help` 查看全部）。跑 `tests/test_web.py` 前必须 `make web-build`。

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
- **升级质检**：启动时 lifespan 跑 `doctor.verify_startup()`，不合格拒绝启动（fail fast）；运行期用 `GET /api/system/health` 或 `opencam system doctor`（schema 版本/完整性、目录可写、快照文件抽查）。
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
- 方案包格式（`packs/` 示例）：`pack.yaml`（id/name/version/vertical/...）+ `rules/*.yaml`（多边形/线用 0-1 相对坐标，apply 时按摄像头分辨率换算）+ 可选 `prompts/*.txt`。
