# 摄像头与视频管理 API 设计

日期：2026-08-19  
状态：待实现  
范围：把摄像头做成完整管理资源，并把上传视频提升为独立资源；补齐实时健康、手动重连、批量启停。

## 背景

现有 `/cameras` 只有半套 CRUD：列表、创建、详情、删除、启停、抓帧、上传文件。规则资源已有 `PUT`，摄像头没有更新接口。README 写了「CRUD」，Web 控制台和 CLI 都不能改名称或源地址。

上传视频只落盘并返回 `path`，没有列表、详情、删除，也没有时长/分辨率。删除摄像头时不处理关联的 `rules` / `events`（模型未声明 cascade）。`CAMERA_ERROR` 常量存在但从不为任何摄像头赋值。RTSP 采集线程已有指数退避自动重连。

## 目标

1. 摄像头：补 `PUT` 更新；删除时清掉规则、事件和事件快照。
2. 运行中禁止改视频源（409）；改名称随时允许。
3. 列表/详情带运行时健康（线程是否活、是否有帧、最近一帧距今多久、分辨率）。
4. 手动重连：对 running 摄像头 stop+start，重置 RTSP 退避。
5. 批量 start/stop，允许部分失败。
6. `/videos` 作为上传文件库：上传、列表、详情（含元数据）、删除；被摄像头引用时不可删。
7. `POST /cameras/upload` 保留为别名，响应向后兼容。
8. CLI 覆盖新接口；Web 摄像头页支持改名/改源和已上传视频列表删除。批量按钮本轮不做。
9. 测试 + `docs/openapi.json` 快照。

## 非目标

- 不引入 `camera.video_id` 外键；摄像头仍用 `source_uri` 字符串。
- 不启用持久化 `CAMERA_ERROR` 状态，不把自动重连中的断线写成 DB `error`。
- 不做 WebSocket / 健康推送。
- 不做「启动全部摄像头」这种无 ids 的接口。
- 不改规则 API、检测流水线、方案包 apply。
- Web 不做批量启停按钮（API 先齐）。

## 架构

两个资源，职责分开：

| 资源 | 职责 | 不负责 |
|---|---|---|
| `/cameras` | 接入配置、生命周期、批量启停、实时健康、手动重连 | 视频文件本身 |
| `/videos` | 上传目录中的文件库（元数据 + 增删） | 采集与分析 |

进程内：健康数据来自 `camera_manager` 里已有的 `CaptureWorker`（`is_alive`、`last_frame_at`、最新帧 shape）。不写库。

`source_uri` 继续是文件绝对路径或 `rtsp://...`。视频库用路径字符串与摄像头关联：删除视频时按 `Camera.source_uri == video.path` 检查引用。

## 数据模型

### Camera（表结构不变）

DB 字段仍为 `id, name, source_type, source_uri, status`。`status` 只表示生命周期：`running` | `stopped`。

Pydantic：

- `CameraCreate`：不变（`name`, `source_type` 匹配 `^(file|rtsp)$`, `source_uri`, `autostart`）。
- `CameraUpdate`：全部可选，至少提供一个字段，否则 422。
  - `name: Optional[str]`
  - `source_type: Optional[str]`，pattern 与创建相同
  - `source_uri: Optional[str]`
  - 不含 `status`、不含 `autostart`
- `CameraOut`：现有字段 + `health: Optional[CameraHealth] = None`
- `CameraHealth`：
  - `alive: bool` — 采集线程仍在跑
  - `has_frame: bool` — 缓冲里有最新帧
  - `last_frame_age_sec: Optional[float]` — 至少成功推过一帧后为 `time.monotonic() - worker.last_frame_at`；`last_frame_at == 0`（初值）视为从未出帧，此时为 `null`
  - `width: Optional[int]`、`height: Optional[int]` — 来自最新帧 shape；无帧则为 `null`

已停止或进程内没有 worker 时，`health` 为 `null`（不是空对象）。RTSP 断线自动重连期间 `status` 仍为 `running`，`alive` 通常为 true，`has_frame` 可能为 false，`last_frame_age_sec` 增大。这就是「流卡住」的信号。

### Video（新表 `videos`）

基线 `origin/main` 用 `Base.metadata.create_all` + 缺列 ALTER，没有 Alembic。本需求只新增 `videos` 表：加入 ORM 后 `create_all` 会建不存在的表。不要在本任务引入 Alembic。字段：

| 列 | 类型 | 说明 |
|---|---|---|
| id | Integer PK | 自增 |
| filename | String(256) | 安全化后的原始文件名（含扩展名） |
| path | Text unique | 磁盘绝对路径，与 `Camera.source_uri` 对齐 |
| size_bytes | Integer | 文件大小 |
| duration_sec | Float nullable | OpenCV 探测；失败为 null |
| width | Integer nullable | 探测失败为 null |
| height | Integer nullable | 探测失败为 null |
| created_at | Float | Unix 时间戳，与 `Event.ts` 一致 |

探测：上传落盘后 `cv2.VideoCapture(path)` 读 `CAP_PROP_FRAME_COUNT` / `CAP_PROP_FPS` / `CAP_PROP_FRAME_WIDTH` / `CAP_PROP_FRAME_HEIGHT`。打不开、fps≤0、frame_count≤0 时时长为 null；宽高读不到则为 null。测试里的假字节文件必须能入库，元数据为 null。

`VideoOut`：上述全部字段。无单独 Update 接口（不改文件内容、不改名）。

## HTTP 接口

### 摄像头（既有，行为补充）

| 方法 | 路径 | 变化 |
|---|---|---|
| GET | `/cameras` | 响应增加 `health` |
| GET | `/cameras/{id}` | 同上；404 文案保持「摄像头不存在」 |
| POST | `/cameras` | 不变 |
| DELETE | `/cameras/{id}` | 见删除语义 |
| POST | `/cameras/{id}/start` | 不变（已 running 则幂等返回） |
| POST | `/cameras/{id}/stop` | 不变（已 stopped 则幂等返回） |
| GET | `/cameras/{id}/snapshot.jpg` | 不变 |
| POST | `/cameras/upload` | 别名，见视频节 |

### 摄像头更新

`PUT /cameras/{id}` → 200 `CameraOut`

1. 摄像头不存在 → 404。
2. body 无任何字段 → 422。
3. 若请求包含 `source_type` 或 `source_uri`（即使值与当前相同），且当前 `status=running` → **409**，detail：`请先停止摄像头再修改视频源`。
4. 只改 `name`：运行中允许，不碰采集/分析线程。
5. 通过校验后写入提供的字段，commit，refresh，带上 `health` 返回。

「包含源字段即 409」是刻意选择：客户端不必比较新旧值；要换源必须先停。

### 摄像头删除

`DELETE /cameras/{id}` → 204

顺序：

1. 不存在 → 404。
2. `status=running` 则先 `stop_camera`。
3. 查询该摄像头全部 `Event`：若 `snapshot_path` 非空、文件存在、且路径落在 `settings.snapshot_dir` 下，则删除文件（路径穿越则跳过，只记日志）。
4. 删除这些 Event 行。
5. 删除该摄像头全部 Rule 行。
6. 删除 Camera 行并 commit。
7. **不**删除 `uploads/` 中的视频文件。

本轮不在 SQLite 上加 `ON DELETE CASCADE`，契约是应用层按上述顺序删。

### 手动重连

`POST /cameras/{id}/reconnect` → 200 `CameraOut`

- 不存在 → 404。
- `status != running` → 409，detail：`仅运行中的摄像头可以重连`。
- 否则 `stop_camera` 再 `start_camera`（与幂等 start 不同，必须真正重启，以重置 RTSP 退避）。
- 启动失败 → 500，detail 前缀 `重连失败:`（与现有 start 的 `启动失败:` 对称）。

### 批量启停

`POST /cameras/batch/start`  
`POST /cameras/batch/stop`

请求：

```json
{ "ids": [1, 2, 3] }
```

`ids` 必填，至少 1 个元素，否则 422。按数组顺序处理，互不影响。

路由必须注册在 `/{camera_id}/...` 之前，避免 `batch` 被当成 id。`camera_id` 已是 int，正常不会匹配，但仍按「静态段优先」声明。

响应 200：

```json
{
  "results": [
    { "id": 1, "ok": true },
    { "id": 2, "ok": false, "error": "摄像头不存在" }
  ]
}
```

`ok: true` 时不带 `error`。单路语义与现有 start/stop 相同（含幂等）。单路启动异常记入该路 `error` 字符串，不把整个请求变成 500。HTTP 状态始终 200（请求本身合法）；空结果只在 422 时出现。

不提供无 `ids` 的「全部启动」。

### 视频

上传目录仍为 `settings.data_dir / "uploads"`。文件名安全化规则与现有一致：`re.sub(r"[^\w.()-]+", "_", filename)`；重名追加 `_1`、`_2`。扩展名白名单不变：`.mp4 .avi .mov .mkv .webm .m4v .ts`。

| 方法 | 路径 | 状态 | 行为 |
|---|---|---|---|
| POST | `/videos` | 201 | multipart `file`；落盘 + 探测 + 插 `videos` 行；返回 `VideoOut` |
| GET | `/videos` | 200 | 按 `id` 升序 |
| GET | `/videos/{id}` | 200 | 404：「视频不存在」 |
| DELETE | `/videos/{id}` | 204 | 见下 |
| POST | `/cameras/upload` | 201 | 与 `POST /videos` 同一套入库；响应在 `VideoOut` 上保证含 `path`（现有 Web 只读 `path`） |

删除视频：

1. 不存在 → 404。
2. 任一 `Camera.source_uri == video.path` → 409，detail：`视频正被摄像头使用，无法删除`。
3. 删磁盘文件（不存在则忽略）+ 删行。

没有视频更新接口，没有从视频一键创建摄像头的接口（客户端用返回的 `path` 调 `POST /cameras`）。

## CLI

`opencam/cli.py` 只允许轻量依赖，继续用 httpx 调 REST。

摄像头新增：

- `opencam cameras update ID [--name] [--source-type file|rtsp] [--source-uri]`  
  至少一个可选参数，否则 argparse 报错。PUT `/cameras/{id}`。
- `opencam cameras reconnect ID` → POST `/cameras/{id}/reconnect`
- `opencam cameras batch-start ID [ID ...]` → POST `/cameras/batch/start`
- `opencam cameras batch-stop ID [ID ...]` → POST `/cameras/batch/stop`

视频新增子命令 `videos`：

- `videos list`
- `videos get ID`
- `videos upload PATH`（multipart；CLI 目前 `_request` 只发 JSON，需为这一条走 `files=`）
- `videos delete ID`

`cameras upload` 不做第二套命令，上传走 `videos upload`。

## Web 控制台

`opencam/web/pages/cameras.js`：

- 列表行可改名称；file/rtsp 源可改。保存走 PUT。409/其它错误用现有 toast。
- 上传成功后除填 `source_uri` 外，刷新已上传视频列表。
- 视频列表：文件名、大小、时长/分辨率（null 显示「—」）、删除按钮。删除 409 时 toast 服务端文案。

仪表盘可直接用列表里的 `health`，本轮不强制改 `dashboard.js`。不做批量按钮。

## 验证方案

测试分层，全部走 `tmp_settings` + `OPENCAM_DETECTOR=mock`，不下载 YOLO、不打外网。命令一律 `uv run pytest …`。

| 层 | 目的 | 落点 |
|---|---|---|
| 契约 | HTTP 状态码 + 规格里的固定 `detail` 文案 | `test_videos_api.py` / `test_cameras_api.py` |
| 持久化 | DB 行与磁盘文件同时对得上（上传在、删除不在、引用保护） | 同上，断言 `Path.exists()` 与 ORM count |
| 安全 | 文件名路径穿越进不了 `uploads` 以外；级联删除不删 `snapshot_dir` 外的文件 | `test_videos_api.py` / `test_cameras_api.py` |
| 探测 | 假字节元数据为 null；用 OpenCV 写的小 mp4 能读出宽高/时长 | `test_videos_api.py` |
| 兼容 | `POST /cameras/upload` 仍返回 `path`；旧 `test_upload_api.py` 全绿 | 别名用例 + 回归 |
| 客户端 | CLI 走 ASGI transport；Web 只做静态源码断言（无浏览器） | `test_cli.py` / `test_web.py` |
| Schema | 运行中的 OpenAPI 含新路径；导出快照 | `test_openapi_cameras.py` + `scripts/export_openapi.py` |
| 回归 | 每阶段相关文件绿；Task 4 关门 `uv run pytest` 全绿 | CI / PR |

不测：真实 RTSP、真实 YOLO、浏览器点击、并发压测、WebSocket。

### 用例矩阵（必须全部有自动化用例）

**视频库 `tests/test_videos_api.py`**

| 用例 | 期望 |
|---|---|
| 空列表 | GET `/videos` → `[]` |
| 假字节上传 | 201；`id/path/filename/size_bytes`；`duration_sec/width/height` 为 null；文件在 `data_dir/uploads` |
| 列表 + 详情 | GET 列表含该条；GET id 字段与上传一致 |
| upload 别名 | POST `/cameras/upload` 201 且 body 含 `path` |
| 非法扩展名 | `.txt` → 400，detail 含 `不支持的视频格式` |
| 重名不覆盖 | 两次 `a.avi` 路径不同，先上传的文件内容不变 |
| 文件名路径穿越 | 文件名 `../../evil.mp4` → 201，落盘路径 `resolve()` 后仍在 `uploads/` 下，不含 `..` |
| 未引用可删 | DELETE 204，文件消失，再 GET 404 |
| 被摄像头引用 | DELETE 409，`视频正被摄像头使用，无法删除`，文件仍在 |
| 删摄像头后再删视频 | 先 DELETE camera 204，再 DELETE video 204 |
| 视频不存在 | GET/DELETE 999 → 404，`视频不存在` |
| 真小 mp4 探测 | OpenCV 写 320×240、约 20 帧的 mp4 上传后 `width==320`、`height==240`、`duration_sec > 0` |

**摄像头 `tests/test_cameras_api.py`**

| 用例 | 期望 |
|---|---|
| 创建响应 | 201 且 `health is None` |
| stopped health | GET 详情与列表均为 `health: null` |
| 运行中只改名 | DB `status=running` 时 PUT `{name}` → 200 |
| 运行中改 uri | PUT `{source_uri}` → 409，库中 uri 不变 |
| 运行中改 type | PUT `{source_type: rtsp}` → 409，文案相同 |
| 停止后改源 | PUT uri → 200 |
| 空 body | PUT `{}` → 422 |
| 非法 source_type | PUT `{source_type: "ftp"}` → 422 |
| 未知 id | GET/PUT/DELETE 999 → 404 |
| 级联删除 | 有规则+事件+snapshot_dir 内快照时 DELETE 204；规则/事件行 0；快照文件消失；uploads 仍在 |
| 无关联删除 | 无规则无事件的摄像头 DELETE 204 |
| 快照路径安全 | `snapshot_path` 指向 `snapshot_dir` **之外** 的文件时，DELETE 摄像头后该文件仍在 |
| 重连 stopped | 409，`仅运行中的摄像头可以重连` |
| 重连 running | 200（或 500 且 detail 以 `重连失败:` 开头，与 `start_camera` 是否抛错一致） |
| 重连 404 | POST `/cameras/999/reconnect` → 404 |
| 批量部分失败 | `ids: [存在, 999]` → 200，前者 ok true，后者 ok false 且 error 含 `摄像头不存在`；`results` 顺序与请求 ids 一致 |
| 批量 stop 幂等 | 已 stopped → ok true |
| 空 ids | `{ids: []}` → 422 |
| 缺 ids | `{}` → 422 |

**CLI `tests/test_cli.py`（现有 TestClient monkeypatch）**

| 用例 | 期望 |
|---|---|
| `cameras update ID --name` | JSON `name` 已变 |
| `cameras update ID` 无可选参数 | 非零退出，stderr 含「至少指定」 |
| `videos upload` + `list` + `get` | id/path 一致 |
| `videos delete` | 后再 list 为空 |
| `cameras reconnect` 对 stopped | 退出码 1，stderr 含 `仅运行中的摄像头可以重连` |
| `cameras batch-start ID 999` | JSON results 一条 ok 一条 false |

**Web / OpenAPI**

| 用例 | 期望 |
|---|---|
| `test_web.py` | `cameras.js` 含 `/videos`、`data-act="save"`（或单引号写法） |
| `tests/test_openapi_cameras.py` | `app.openapi()["paths"]` 含 `/videos`、`/cameras/{camera_id}` 的 put、`/cameras/batch/start`、`/cameras/{camera_id}/reconnect` |
| 导出 | Task 4 跑 `uv run python scripts/export_openapi.py`，快照与上式路径一致 |

改接口后必须重导 OpenAPI。README 摄像头表格补上 PUT、reconnect、batch、`/videos`。`skills/opencam/SKILL.md` 同步典型命令。`main.py` 挂载 videos 后 AGENTS.md 的 `api/` 枚举加 `videos`。

## 错误文案（固定，测试可按子串断言）

| 场景 | HTTP | detail |
|---|---|---|
| 摄像头不存在 | 404 | 摄像头不存在 |
| 运行中改源 | 409 | 请先停止摄像头再修改视频源 |
| 已停止却重连 | 409 | 仅运行中的摄像头可以重连 |
| 启动失败 | 500 | 启动失败: … |
| 重连失败 | 500 | 重连失败: … |
| 视频不存在 | 404 | 视频不存在 |
| 视频正被使用 | 409 | 视频正被摄像头使用，无法删除 |
| 上传扩展名非法 | 400 | 不支持的视频格式 …（现有句式） |

## 实现落点

- `opencam/models.py`：`CameraUpdate`、`CameraHealth`、`CameraOut.health`、`Video` ORM、`VideoOut`
- `opencam/api/cameras.py`：PUT、删除级联、reconnect、batch、CameraOut 填 health；upload 改为调用视频入库
- `opencam/api/videos.py`：新路由，prefix `/videos`
- `opencam/main.py`：挂载 videos router；OpenAPI tag
- `opencam/cli.py`、`opencam/web/pages/cameras.js`
- 测试与 `docs/openapi.json`、README、skill

采集层不强制改：health 只读现有 `CaptureWorker`。若最新帧拿得到 shape，用 `frame.shape[1]`/`shape[0]` 作宽高，不必再打开一次 VideoCapture。

## 验收

1. 摄像头可改名；运行中改源 409，停止后改源 200。
2. 删除有规则/事件的摄像头成功，无孤儿行，快照文件被删，上传视频仍在。
3. 列表中 running 摄像头带 `health` 对象，stopped 为 `null`。
4. 批量启停返回逐路 `ok`；非法空 ids 为 422。
5. 视频可上传/列出/删除；被摄像头引用时 409。
6. `POST /cameras/upload` 旧客户端只读 `path` 仍可用。
7. **验证方案矩阵全部有对应自动化用例**；`uv run pytest` 全绿；openapi 快照已更新。
8. 路径穿越文件名不能写到 `uploads/` 外；级联删除不能删 `snapshot_dir` 外的文件。
9. 真小 mp4 上传后能读出正的宽高与时长；假字节元数据为 null。
