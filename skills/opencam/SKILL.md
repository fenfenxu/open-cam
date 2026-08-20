---
name: opencam
description: Use when the user asks about open-cam cameras, unacked alerts or events, footfall stats, industry packs, live snapshots, or detection rules.
---

# opencam

open-cam 是视频监控分析服务（RTSP/文件 → YOLO → 规则告警 → VLM 复核 → 事件入库）。唯一入口是 `opencam` CLI（仓库内用 `uv run opencam`）。

## 输出契约

- 成功 stdout 永远是紧凑 JSON，直接 `json.loads`；**不要加 `--pretty`**（那是给人看的）。
- 删除/卸载返回 `{"ok":true,"id":...}`；snapshot 返回 `{"ok":true,"path":...,"bytes":...}`。
- 失败时信息在 stderr、退出码 1，stdout 不掺成功 JSON。
- 事件 `ts` 是 Unix 秒。列表默认 `--page-size 20`，返回满页必须再用 `--offset` 翻页拉完。
- 服务地址：`--base-url` 或 `OPENCAM_BASE_URL`，默认 `http://127.0.0.1:8600`。

## 发现命令

命令树以 `--help` 为准，不要凭记忆或本文抄参数：

```bash
opencam --help
opencam events --help
```

其余资源（cameras、rules、packs、stats、videos、system、models）同理，用 `opencam <resource> --help` 查看。事件片段下载等冷门参数见 `opencam events --help`。CLI 没有的接口用 `opencam api METHOD PATH`，不要 curl。

## 工作流：查待办告警

```bash
opencam events list --needs-action true
opencam events get 42
opencam events ack 42
```

1. `events list --needs-action true` 拉待办事件（观察记录不算待办）；返回满 `--page-size` 条时用 `--offset` 继续拉，可按 `--camera-id` / `--type` 过滤。旧版 `--acked false` 只看未确认，不能区分观察与待办。
2. 逐条 `events get <id>` 看详情（含 VLM 判定 `vlm_verdict` 与理由）。
3. 向用户说明判定依据后，才 `events ack <id>` 确认。

## 工作流：加区域规则

```bash
opencam cameras get 1
opencam cameras snapshot 1 -o cam1.jpg
opencam rules presets
opencam rules create 1 --type zone_count --name 排队超员 --params '{"polygon": [[120,80],[900,80],[900,700],[120,700]], "threshold": 5}'
```

1. `cameras get` 确认摄像头存在与分辨率。
2. `cameras snapshot` 抓当前帧，**多边形像素坐标必须从这张图上量**。
3. `rules presets` 查五种规则类型的参数说明。
4. `rules create` 用刚量出的像素坐标建规则。

量不到坐标就问用户要，**禁止**凭 1080p 经验编造矩形——假坐标会产生永远不触发或乱触发的规则。

## 工作流：部署训练模型

```bash
opencam models list
opencam models compare 2
opencam models deploy 2
opencam models rollback 2
```

1. `models list` 看已登记版本的指标、产物路径、来源任务与 `status`（registered / live / previous）。
2. `models compare <id>` 与同槽位线上模型比三项指标（accuracy / recall / false_alarm_per_day）；只有全面更优才建议替换。
3. `models deploy <id>` 上线；未更优会 HTTP 409。店主明确要求时才加 `--force`。
4. `models rollback <id>` 按该版本所在槽位恢复上一线上版本，回滚入口常驻。

登记新产物：`opencam models register --help`（默认读 `eval.json` 与 `best.pt`）。

## 事件字段速查

- `type`：zone_intrusion 区域入侵 / loitering 徘徊 / object_count 人数统计 / zone_count 区域人数 / line_crossing 越线计数。
- `vlm_status`：pending / skipped / done / failed；`vlm_verdict`：confirmed / false_alarm / uncertain。
- `acked`：是否已确认。
