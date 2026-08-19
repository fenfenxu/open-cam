---
name: opencam
description: 查询与控制 open-cam 本地视频监控系统（摄像头、规则、告警事件、方案包、客流统计）。当用户想查看摄像头状态、查询/确认告警、看分时段客流、给摄像头应用行业方案包、抓实时画面时使用。服务默认 http://127.0.0.1:8600，CLI 为 opencam。
---

# opencam

open-cam 是本地视频流分析服务（RTSP/视频文件 → YOLO 检测 → 规则告警 → VLM 复核 → 事件入库），**视频数据不出本机**。通过 `opencam` CLI 操作（资源式子命令，默认输出紧凑 JSON，`--pretty` 美化）。

## 前提

- 服务已启动（`uv run uvicorn opencam.main:app --port 8600`）。
- CLI 已随包安装：`uv run opencam ...`（仓库内），或 `uv tool install` 后直接 `opencam ...`。
- 服务地址：`--base-url` 或环境变量 `OPENCAM_BASE_URL`，默认 `http://127.0.0.1:8600`。
- 完整接口文档：`{base_url}/docs`（Swagger UI）与 `{base_url}/redoc`。

## 典型任务

### 查最近的告警事件

```bash
opencam events list --pretty
opencam events list --acked false --camera-id 1        # 只看 1 号摄像头未确认的
opencam events list --type zone_intrusion              # 按类型过滤
opencam events get 42 --pretty                         # 详情（含 VLM 判定与理由）
```

### 确认（ack）告警

```bash
opencam events ack 42
```

### 看分时段客流统计

```bash
opencam stats footfall --camera-id 1 --pretty          # 今天 24 小时进/出分桶
opencam stats footfall --camera-id 1 --date 2026-08-19 # 指定日期
```

### 给摄像头应用方案包

```bash
opencam packs list                                     # 浏览内置/已安装包
opencam packs apply fast-food 1                        # 应用到 1 号摄像头
opencam rules list 1                                   # 确认规则已生成
```

### 摄像头与规则管理

```bash
opencam cameras list
opencam cameras create --name 门口 --source-type file --source-uri /v/demo.mp4 --autostart
opencam cameras start 1 / stop 1 / delete 1
opencam cameras snapshot 1 -o /tmp/cam1.jpg            # 抓实时帧

opencam rules presets                                  # 五种规则的引导元数据
opencam rules create 1 --type zone_count --name 排队超员 \
  --params '{"polygon": [[0,0],[320,0],[320,240],[0,240]], "threshold": 5}'
opencam rules delete 1 3                               # 摄像头 1 的规则 3
```

### 系统状态

```bash
opencam system info          # 推理设备/模型/方案包统计/VLM 配置
```

## 事件字段速查

- `type`：zone_intrusion 区域入侵 / loitering 徘徊逗留 / object_count 人数统计 / zone_count 区域人数 / line_crossing 越线计数。
- `vlm_status`：pending / skipped（无 key）/ done / failed；`vlm_verdict`：confirmed / false_alarm / uncertain。
- `detail`：命中目标框、track id、数量；line_crossing 另有 `direction`（in/out）与 `crossings`。
- `acked`：是否已确认。

## 兼容旧脚本

`scripts/opencam_client.py` 保留旧子命令（events/status/snapshot/ack），内部转发到新 CLI；新任务请直接用 `opencam`。
