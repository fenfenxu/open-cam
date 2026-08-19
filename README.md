# open-cam

摄像头视频流分析与监控管理工具：接入 RTSP / 视频文件流，YOLO + 规则引擎初筛，VLM 抽帧复核，事件入 SQLite，经 Web 控制台 / REST API / Agent Skill / 示例监控 Agent 消费。

## 架构

```
RTSP/File ──► CaptureWorker(线程, 环形帧缓冲)
                   │ 抽帧(可配 fps, 默认 3)
                   ▼
            YOLO 检测 + ByteTrack 跟踪（本机算力 auto→cuda/mps/cpu）
                   │ 命中规则(区域入侵/徘徊/数量阈值)
                   ▼
            Event(存快照) ──► 异步队列 ──► VLM 复核(OpenAI 兼容接口)
                   │                            │ 回填判定
                   ▼                             ▼
              SQLite 事件库 ◄── REST API ◄── Web 控制台 / Skill / 监控 Agent
```

- **算力自适应**：`device: auto` 启动时探测 cuda → mps（Apple Silicon）→ cpu，也可显式指定。
- **采样检测而非逐帧**：默认 3 fps，CPU 可承受。
- **SQLite 单文件**：零运维，模型层用 SQLAlchemy，后续可换 Postgres。
- **VLM 走 OpenAI 兼容协议**：一家客户端代码覆盖多数供应商；api_key 只走环境变量 `OPENCAM_VLM_API_KEY`。
- **Web 控制台**：Vite + React + shadcn/ui。开发：`make web-dev`（需另开 `make run`）。发布：`make web-build` 后 `make run`，浏览器打开 `http://127.0.0.1:8600`。需要 Node 20+。

## 快速开始

```bash
# 安装（需要 uv）
uv venv --python 3.12
uv pip install -e .   # 或：uv pip install fastapi "uvicorn[standard]" opencv-python-headless ultralytics sqlalchemy pydantic-settings pyyaml httpx

# 配置（可选，不建 config.yaml 也能跑默认值）
cp config.example.yaml config.yaml
# VLM 复核 key（可选，不配则事件 vlm_status=skipped）
export OPENCAM_VLM_API_KEY=sk-...

# 无模型环境/CI：切内置 mock detector，不下载 yolov8n.pt
export OPENCAM_DETECTOR=mock

# 启动服务（默认端口 8600）
# 控制台：先 make web-build（Node 20+），或开发时另开 make web-dev
uv run uvicorn opencam.main:app --port 8600
```

控制台点仪表盘卡片进入摄像头详情：运行中显示 MJPEG 直播。仅视频文件源可在详情页拖进度回放；RTSP 直播不支持回放（不会在本机录像）。

- `GET /cameras/{id}/live.mjpg` 实时预览
- `GET /cameras/{id}/source` 文件源原片（RTSP 返回 400）

启动后打开 `http://127.0.0.1:8600/docs`（Swagger UI）或 `http://127.0.0.1:8600/redoc`（ReDoc）查看 API 文档；机器可读的 schema 在 `/openapi.json`。

### 接入一路视频文件

```bash
curl -X POST http://127.0.0.1:8600/cameras \
  -H 'Content-Type: application/json' \
  -d '{"name": "门口", "source_type": "file", "source_uri": "/path/to/video.mp4", "autostart": true}'

# 配置一条全屏区域入侵规则（画面按 1280x720 示例）
curl -X POST http://127.0.0.1:8600/cameras/1/rules \
  -H 'Content-Type: application/json' \
  -d '{"type": "zone_intrusion", "cooldown": 30,
       "params": {"polygon": [[0,400],[1280,400],[1280,720],[0,720]], "classes": ["person"]}}'

# 查事件
curl http://127.0.0.1:8600/events
```

### 用 ffmpeg 推一路本地 RTSP 测试流

```bash
# 1) 起一个本地 RTSP 服务（如 mediamtx），或用 ffmpeg 推到已有服务
ffmpeg -re -stream_loop -1 -i /path/to/video.mp4 -c copy -f rtsp rtsp://127.0.0.1:8554/test

# 2) 以 rtsp 类型创建摄像头
curl -X POST http://127.0.0.1:8600/cameras \
  -H 'Content-Type: application/json' \
  -d '{"name": "测试流", "source_type": "rtsp",
       "source_uri": "rtsp://127.0.0.1:8554/test", "autostart": true}'
```

## API 摘要

交互式文档：Swagger UI `/docs`、ReDoc `/redoc`。仓库内 `docs/openapi.json` 是导出的 schema 快照，改动 API 后重新生成：

```bash
uv run python scripts/export_openapi.py
```

| 方法与路径 | 说明 |
|---|---|
| `GET/POST /cameras` | 摄像头列表 / 创建（`autostart` 可创建即启动） |
| `GET/DELETE /cameras/{id}` | 详情 / 删除（运行中会自动停止；级联规则、事件与快照，不删 uploads） |
| `PUT /cameras/{id}` | 仅更新名称（改类型/视频源 409，请新建摄像头） |
| `POST /cameras/{id}/start`、`/stop` | 启停采集与分析流水线 |
| `POST /cameras/{id}/reconnect` | 重连运行中的摄像头（stopped 为 409） |
| `POST /cameras/batch/start`、`/batch/stop` | 批量启停（body `{ids}`，空列表 422） |
| `GET /cameras/{id}/snapshot.jpg` | 当前实时帧 JPEG |
| `GET /cameras/{id}/live.mjpg` | 实时 MJPEG 预览（未运行或无帧 503） |
| `GET /cameras/{id}/source` | 文件源原片回放（仅 `file`；RTSP 返回 400） |
| `GET/POST /videos`、`GET/DELETE /videos/{id}` | 本机上传视频库（被摄像头 `source_uri` 引用时删除 409） |
| `POST /cameras/upload` | 上传别名，与 `POST /videos` 同一套入库，响应含 `path` |
| `GET/POST /cameras/{id}/rules`、`PUT/DELETE .../{rule_id}` | 规则 CRUD（含 `name` 中文字段；旧式 type/params 直传仍兼容） |
| `GET /api/rules/presets` | 规则场景化预设元数据（引导卡片数据源） |
| `GET /api/stats/footfall?camera_id=&date=` | 分时段进出店客流（按本地小时分桶统计越线 in/out） |
| `GET /events` | 事件列表，过滤：`camera_id` `rule_type` `vlm_verdict` `acked` `status` `starred`，分页：`limit` `offset` |
| `GET /events/{id}` | 事件详情（含 VLM 判定与理由、处置状态） |
| `PATCH /events/{id}` | 处置编辑：状态（open/acked/resolved/ignored）/ 关注星标 / 负责人 / 备注，变更全程留痕 |
| `GET /events/{id}/actions` | 处置时间线（关注/指派/状态/备注/通知的审计记录） |
| `POST /events/{id}/ack` | 确认事件（同步 status=acked） |
| `POST /events/{id}/notify` | 重发通知到匹配的渠道 |
| `GET /events/{id}/snapshot` | 事件快照图 |
| `GET/POST /api/notify-channels`、`PATCH/DELETE .../{id}`、`POST .../{id}/test` | 通知渠道 CRUD 与测试推送（webhook，兼容飞书/企业微信/钉钉机器人；摄像头/规则类型留空表示全部） |
| `GET /api/system/info` | 算力设备 / 内存 / 模型 / 方案包统计 / VLM 配置状态 |
| `GET /api/packs`、`POST /api/packs/install`、`POST /api/packs/{id}/apply`、`DELETE /api/packs/{id}` | 方案包列出 / 安装 / 应用 / 卸载 |
| `GET /api/packs/online` | 在线市场（stub，未配置平台时降级为内置包） |
| `GET /api/account/status`、`POST /api/account/login`、`/logout` | 平台账号（stub，不强制登录） |
| `GET /health` | 健康检查 |
| `GET /` | Web 控制台 |

服务重启时会自动恢复数据库中 `status=running` 的摄像头。

## Web 控制台

浏览器打开 `http://127.0.0.1:8600`：

- **仪表盘**：摄像头卡片网格，运行中的卡片约 1fps 轮询快照做准实时画面，附最近事件数；卡片下方内嵌今日 24 小时进/出客流双列柱状图（数据来自 `/api/stats/footfall`）。卡片可点进详情。
- **摄像头**：CRUD 与启停；详情页 `#/cameras/{id}` 可看 MJPEG 直播，文件源可拖进度回放。
- **规则**：场景引导式三步配置——选场景卡片 → 填参数（默认值+中文提示）→ 画布画多边形 ROI；已有规则显示中文名与参数摘要，叠加显示可删除。
- **事件处置**：时间线 + 摄像头/类型/处置状态/VLM 判定过滤与「仅看关注」，行内星标关注；详情区可流转状态（确认/处置完成/误报忽略）、编辑负责人与备注、重发通知，并展示完整处置时间线。
- **方案市场**：浏览内置方案包、一键应用到摄像头、从本地目录/zip/URL 安装、卸载。
- **设置**：`/api/system/info` 算力与 VLM 配置状态、平台账号状态、通知渠道管理（webhook + 适用范围 + 测试推送）。

## 规则：五种场景

不用理解技术名词，按场景选（`GET /api/rules/presets` 返回引导元数据）：

- **区域入侵**：在画面上圈一块地，有人/车进入就告警。例：顾客进入后厨告警、闭店后有人进店、车辆驶入人行区。
- **徘徊逗留**：同一目标在区域内停留超过设定秒数才告警，路过不算。例：店外可疑人员逗留、等候区客人等待超时提醒。
- **人数统计**：整个画面内某类目标超过设定数量就告警，不用画区域。例：店内超员提醒、门口人群异常聚集。
- **区域人数**：只统计你画出的区域内的目标数，超阈值告警。例：点餐区排队超 5 人、收银台排长队提醒。
- **越线计数**：在画面画一条线（两个点），目标穿越即计数，可区分进/出方向（约定：沿线第一点→第二点看，左手侧穿到右手侧为"进"）。同一目标须回到原侧才再次计数。例：门口进出店客流、车辆进出场计数。

所有规则都支持可选的**生效时段** `active_hours`（如 `22:00-07:00`，支持跨午夜；留空=全天生效）。快餐店场景的完整需求全景见 `docs/fastfood-analytics.md`。

## 解决方案包

方案包是一组行业规则模板的目录（或 `.zip`）：

```
my-pack/
├── pack.yaml      # id/name/version/vertical/description/author/min_opencam_version
├── rules/*.yaml   # 规则模板: name/type/cooldown/params（polygon/line 用 0-1 相对坐标）
├── prompts/*.txt  # 可选: 行业 VLM 复核提示词
└── README.md      # 说明
```

规则模板示例：

```yaml
name: 收银台长时间徘徊
type: loitering        # zone_intrusion / loitering / object_count
cooldown: 300
params:
  polygon: [[0.6, 0.4], [1.0, 0.4], [1.0, 1.0], [0.6, 1.0]]  # 相对坐标
  duration: 120
  classes: [person]
```

应用到摄像头时，相对坐标按该摄像头画面分辨率换算为绝对像素，之后就是普通规则，可在 Rules 页自由修改。

仓库内置四个示例包（`packs/`）：`retail-chain`（连锁零售）、`salon`（美容美发）、`restaurant`（餐饮）、`fast-food`（餐饮-快餐，含越线计数与闭店时段规则）。第三方作者按上述格式打包（目录或 zip）即可通过 `POST /api/packs/install` 或控制台安装；在线市场平台为预留 stub，平台后端后续接入。

## 平台账号（预留）

不强制登录。`platform_base_url` 与 token 存 `data/account.json`，供以后市场平台使用；未配置平台时 `POST /api/account/login` 返回明确说明，市场"在线浏览"降级为只显示内置包。

## CLI

安装后（`uv pip install -e .` 或 `uv tool install .`）即可使用 `opencam` 命令；仓库内开发用 `uv run opencam ...`。资源式子命令覆盖全部 API，默认紧凑 JSON 输出（`--pretty` 美化），服务地址用 `--base-url` 或 `OPENCAM_BASE_URL` 指定：

```bash
opencam cameras list                                # 摄像头列表
opencam cameras create --name 门口 --source-type file --source-uri /v.mp4 --autostart
opencam cameras start 1 | stop 1 | snapshot 1 -o cam1.jpg
opencam rules list 1 | presets                      # 规则与场景预设
opencam rules create 1 --type zone_count --params '{"threshold": 5}'
opencam events list --acked false                   # 未确认事件
opencam events ack 42
opencam packs list | apply fast-food 1              # 方案包
opencam stats footfall --camera-id 1                # 分时段客流
opencam system info                                 # 算力与配置
```

## Skill 安装

把 `skills/opencam/` 拷到 agent 的 skill 目录即可：

```bash
cp -r skills/opencam ~/.agents/skills/
```

之后 agent 按 SKILL.md 指引使用 `opencam` CLI 完成查事件、确认告警、看客流、应用方案包等任务；旧脚本 `opencam_client.py`（events/status/snapshot/ack）保留为兼容 wrapper。

## 示例监控 Agent

`agent/monitor_agent.py`：轮询未确认事件 → LLM 生成定级与处置建议（无 `OPENCAM_AGENT_API_KEY` 时退化为规则化模板）→ 可选 webhook 推送 → 自动 ack。

```bash
export OPENCAM_AGENT_API_KEY=sk-...   # 可选，OpenAI 兼容 LLM
uv run python agent/monitor_agent.py \
  --base-url http://127.0.0.1:8600 \
  --interval 5 \
  --webhook https://example.com/hook   # 可选
```

## 测试

```bash
uv run pytest        # 规则单测 + API 冒烟 + 端到端（mock detector，不下载模型）
```

## 环境变量一览

| 变量 | 说明 |
|---|---|
| `OPENCAM_CONFIG` | 配置文件路径，默认 `config.yaml` |
| `OPENCAM_DETECTOR` | `yolo` / `mock`，优先级高于配置文件 |
| `OPENCAM_DEVICE` | 推理设备：`auto`（默认）/ `cpu` / `mps` / `cuda` / `cuda:0` |
| `OPENCAM_VLM_API_KEY` | VLM 复核 api key（唯一来源，不写文件） |
| `OPENCAM_DETECT_FPS` 等 | 任意配置字段的 `OPENCAM_` 大写形式可覆盖 yaml |
| `OPENCAM_AGENT_API_KEY` | 示例 Agent 的 LLM key |
