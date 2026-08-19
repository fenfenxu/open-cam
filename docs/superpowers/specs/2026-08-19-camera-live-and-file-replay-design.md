# 摄像头直播预览与文件回放

日期：2026-08-19  
状态：待实现  
范围：摄像头详情页持续预览采集画面；仅当源是本地视频文件时提供进度条回放。不在本机录像。

## 背景

仪表盘卡片约 1fps 轮询 `GET /cameras/{id}/snapshot.jpg`，卡片不可点。摄像头页是启停表，没有详情。浏览器不能直接播 `rtsp://`。

事件页对**文件源**已能通过 `/events/{id}/clip` 从原文件切片回放；RTSP 事件只有快照。这与「源自己能不能按时间定位」一致，本需求不改变事件切片策略。

用户原则：源支持回放才接入；不支持就算了。禁止为了回放而本机录制再播。

## 目标

1. 仪表盘卡片、摄像头列表可进入详情（`#/cameras/{id}`）。
2. 运行中的摄像头详情持续显示采集画面（MJPEG，从现有 `CaptureWorker` 环形缓冲取最新帧）。
3. `source_type=file` 且源文件存在时，详情页提供 `<video controls>`，可拖进度条，请求走 `GET /cameras/{id}/source`（支持 HTTP Range）。
4. `source_type=rtsp` 不提供回放控件，展示固定中文说明：直播流不支持回放。
5. 测试覆盖新 API；改接口后重导 `docs/openapi.json`。Web 冒烟断言详情页与直播/回放 URL。

## 非目标

- 不为本机或 RTSP **录像**、不写环形 mp4、不把内存帧编成 RTSP 告警短片。
- 不对接 ONVIF / 海康回放 URL / MediaMTX record。
- 不把 RTSP 转封装成 HLS/WebRTC 给浏览器（直播只用 MJPEG）。
- 不改检测流水线、规则、事件 clip 窗口（仍是命中前 2s / 后 3s，仅文件源）。
- 不改 CLI（MJPEG/文件流是给控制台的）。
- 不做多路同页「电视墙」以外的布局改造；仪表盘仍是卡片网格，只是可点进详情。
- 不把直播画面与文件播放器的时间轴强制对齐（文件循环采集位置 ≠ `<video>` 当前进度）。

## 能力判定（写死，不探测协议）

| `source_type` | 直播预览 | 回放 |
|---|---|---|
| `file` | 运行中：MJPEG | 源文件存在：`<video>` seek |
| `rtsp` | 运行中：MJPEG | 无。文案：「该源为直播流，不支持回放。」 |

不根据 RTSP DESCRIBE、不根据文件编码「探测」是否可 seek。容器浏览器播不了（如部分 `.avi` / `.mkv`）时：仍提供 `<video src>`，`error` 时换成与事件页同类的提示（浏览器无法播放该格式）。

## 架构

```
采集线程 CaptureWorker（已有 deque）
        │ latest_frame()
        ▼
GET /cameras/{id}/live.mjpg  ──► 详情页 <img>     （file / rtsp 相同）
GET /cameras/{id}/source     ──► 详情页 <video>   （仅 file，FileResponse + Range）
GET /cameras/{id}/snapshot.jpg 保持不变（仪表盘 1fps 缩略图）
```

浏览器不拉 `rtsp://`。文件回放读的是摄像头配置的那份源文件，不是检测过程另存的录像。

## API

均挂在现有 `opencam/api/cameras.py`。

### `GET /cameras/{camera_id}/live.mjpg`

- 摄像头不存在：404，detail `摄像头不存在`。
- `status != running` 或尚无帧：503，detail `暂无可用帧（摄像头未运行或流未就绪）`（与 snapshot 一致，不建立长连接）。
- 成功：`StreamingResponse`，`Content-Type: multipart/x-mixed-replace; boundary=frame`。
- 循环：`latest_frame()` → JPEG（`IMWRITE_JPEG_QUALITY=80`）→ 一块 part；间隔 **0.125s**（约 8fps）。无帧时跳过该拍、继续睡，不拆连接。
- 编码失败：跳过该拍。
- 客户端断开：生成器退出（`GeneratorExit` / 写失败），不留后台线程。
- 循环中若摄像头已 stop：结束生成器（连接关闭即可，不必再发 JSON 错误）。
- 不经过 YOLO、不加检测框；就是采集缓冲里的原始帧。

### `GET /cameras/{camera_id}/source`

- 摄像头不存在：404，`摄像头不存在`。
- `source_type != file`：400，`该源为直播流，不支持文件回放`。
- 用现有 `clip.resolve_source_uri` 解析 `source_uri`；不是文件：404，`源文件不存在`。
- 成功：`FileResponse`，`media_type` 用 `clip.media_type_for`，`filename` 为路径名。Starlette 的 FileResponse 已支持 Range，供 `<video>` seek。
- 安全：只返回该摄像头自己的 `source_uri` 解析结果，不接受查询参数里的路径。

不新增 ORM 字段。

## Web

### 路由

当前 `app.js` 用 hash 整段匹配（`#/cameras`）。改为分段：

- `#/dashboard` 等保持不变。
- `#/cameras` → 列表（现 `cameras.js`）。
- `#/cameras/{id}`（id 为正整数）→ 新页 `opencam/web/pages/camera.js`。
- 侧栏「摄像头」在详情时仍高亮（`data-route` 前缀为 `cameras`）。

### 详情页 `camera.js`

- 标题：摄像头名称 + status badge；返回链接到 `#/cameras`。
- 元信息：`source_type · source_uri`（等宽）。
- **直播**：`status==running` 时 `<img class="cam-live" src="/cameras/{id}/live.mjpg" alt="直播">`；否则提示未运行，可显示一张静态 `snapshot.jpg`（失败则占位）。
- **回放**：`source_type==file` 时 `<video class="cam-replay" controls playsinline src="/cameras/{id}/source">`，`error` 时替换为 dim 提示。`rtsp` 只显示那句不支持回放的说明。
- 启停按钮：复用现有 start/stop API，成功后重新 `render`。
- 离开页面：`img.src=''` 以断开 MJPEG（cleanup 回调）。

### 入口

- 仪表盘卡片可点（`cursor:pointer`），`location.hash = #/cameras/{id}`；点击客流图不要求单独处理（整卡可点即可）。
- 摄像头表增加「查看」或行点击进入详情；启停/删除按钮 `stopPropagation`。

### 样式

直播图与回放视频最大宽度铺满主栏，高度自适应，深色背景，与现有 `.cam-shot` 风格一致。

## 测试

新文件 `tests/test_camera_live_replay.py`（`tmp_settings` + TestClient）：

| 用例 | 期望 |
|---|---|
| 不存在的 id 的 live.mjpg / source | 404 |
| 未启动 file 摄像头 live.mjpg | 503 |
| rtsp 摄像头 GET source | 400，detail 含「直播流」 |
| file 摄像头、磁盘上有小 mp4 | source 200，body 非空，content-type 含 video |
| file 但文件缺失 | source 404 |
| 启动后有帧时 live.mjpg | 200，content-type 含 `multipart/x-mixed-replace`；读流拿到 JPEG SOI `\xff\xd8` 后关闭 |

Web：`test_web.py` 增加 `camera.js` 静态资源；断言含 `live.mjpg`、`/source`、以及 RTSP 不支持回放的文案。

e2e 不强制跑 MJPEG 长连接（TestClient 短读即可）。

## 文档与其它

- `scripts/export_openapi.py` 更新快照。
- README 当前无 API 表：在「运行」节后补 `live.mjpg` / `source` 两行说明。
- 不改 Skill（无新 CLI）。

## 实现顺序

1. API：`source` + `live.mjpg` + 单测。
2. `app.js` 分段路由 + `camera.js` + 样式 + 仪表盘/列表入口。
3. OpenAPI + README + `test_web.py`。
