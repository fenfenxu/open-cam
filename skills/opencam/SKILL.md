---
name: opencam
description: 查询与控制 open-cam 摄像头监控系统。当用户想查看摄像头状态、查询监控告警事件（区域入侵/徘徊/数量超限）、确认（ack）告警、抓取摄像头当前画面快照时使用。服务默认运行在 http://127.0.0.1:8600。
---

# opencam

open-cam 是一个本地摄像头视频流分析服务（RTSP/视频文件 → YOLO 检测 → 规则告警 → VLM 复核 → 事件入库）。本 skill 通过其 REST API 查询与控制。

## 前提

- open-cam 服务已启动（`uv run uvicorn opencam.main:app --port 8600`）。
- API 文档（Swagger UI）在 `{base_url}/docs`。

## 使用方式

脚本：`scripts/opencam_client.py`，仅用标准库（有 httpx 时自动加速），`--base-url` 默认 `http://127.0.0.1:8600`。

```bash
# 查看摄像头列表与运行状态
python3 scripts/opencam_client.py status

# 查询最近 20 条事件（JSON 输出）
python3 scripts/opencam_client.py events

# 只查未确认的事件 / 按摄像头 / 按规则类型 / 按 VLM 判定过滤
python3 scripts/opencam_client.py events --acked false
python3 scripts/opencam_client.py events --camera-id 1 --rule-type zone_intrusion
python3 scripts/opencam_client.py events --vlm-verdict confirmed

# 抓取摄像头当前画面
python3 scripts/opencam_client.py snapshot 1 -o /tmp/cam1.jpg

# 确认一条告警
python3 scripts/opencam_client.py ack 42
```

## 事件字段说明

- `type`：规则类型，`zone_intrusion`（区域入侵）/ `loitering`（徘徊滞留）/ `object_count`（数量超限）。
- `vlm_status`：`pending`（待复核）/ `skipped`（无 VLM key 跳过）/ `done` / `failed`。
- `vlm_verdict`：VLM 复核结论，`confirmed` / `false_alarm` / `uncertain`，可能为 null。
- `detail`：命中目标框、track id、数量等上下文。
- `acked`：是否已确认处理。

## 补充

- 事件快照图片：`GET {base_url}/events/{id}/snapshot`。
- 摄像头实时帧：`GET {base_url}/cameras/{id}/snapshot.jpg`。
- 创建摄像头/规则等写操作直接调 REST API（见 `{base_url}/docs`），本脚本只覆盖常用只读与 ack 操作。
