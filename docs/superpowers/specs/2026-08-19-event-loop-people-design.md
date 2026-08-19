# 事件闭环：观察 / 待办 / 员工通知

日期：2026-08-19  
状态：待实现  
范围：把「检测命中」拆成事实记录与待办升格；待办才走判定、处置、员工路由与 IM 推送；经营统计只吃观察事实与已判定待办。

实现按四个可独立发版的切片落地（各写一份 plan，不要一次改完）：

1. 规则意图 + `needs_action` + 待办箱  
2. 策略关卡（立即 / 持续 / 连续 / 复合客流 + 折叠）  
3. 员工、路由、个人 IM；判定与处置拆开  
4. `GET /api/stats/ops` + 客流统计按 intent 过滤

## 背景

当前每条规则命中都写成 `Event`，默认 `status=open`，VLM 与 Notifier 无条件入队。`line_crossing` 已经在给 `GET /api/stats/footfall` 供数，却同时出现在「事件处置」待处理列表。`assignee` 是 64 字自由文本；`NotifyChannel` 是 webhook + 摄像头/规则通配，人不是一等公民。`ignored` 既像误报又像「先不管」。

检测命中 ≠ 必须有人处理的工单。客流大了才叫人、连续发生才建工单、有的只进报表——都是策略，不是新的检测器。

业界对照：Genetec 的 Event → Alarm → Incident（含同位置折叠）；Datadog Monitor 的 `for: 5m`；PagerDuty 的 event → alert → incident。零售例外报告同理：不是每条记录都审。

已拍板：

- 方案 C：Event 仍记事实；策略决定是否升格待办。观察类也落 `events` 表，不用另建时序库。
- 员工先建出来，登录可选；不登录也能当负责人、也能收飞书/钉钉。本期控制台不强制登录，IM 不做按钮回写。
- 路由：摄像头 × 规则类型 → 员工（空值通配）。

## 目标

1. 规则有 `intent`（`observe` | `alert`）；事件带 `intent` 与 `needs_action`。观察类不进待办、不通知、不指派、不做 VLM。
2. 告警经策略关卡后才打开待办；同摄像头+同规则未结待办折叠，不刷工单。
3. 待办上判定（`verdict`）与处置（`status`）分开；误报关闭走 `ignored`，属实才 `acked` → `resolved`。
4. `people` + 个人渠道 + 路由；新建待办时指派并推 IM。现有群机器人 `NotifyChannel` 保留作兜底。
5. 客流统计不把告警当客流；新增当日经营摘要接口。

## 非目标

- 登录鉴权、密码、会话、「我的待办」权限隔离。
- 飞书/钉钉卡片按钮回写状态。
- 排班、岗位角色、一人多店 ACL。
- 任意指标表达式引擎；复合条件只做「当日进店 ≥ N」。
- 跨摄像头策略、转化率/翻台率等仍缺检测能力的经营指标。
- 把旧 `NotifyChannel` 自动改写成员工（群机器人不是人）。
- 另建时序数据库。

## 产品闭环

```text
检测命中
  ├─ observe ──► 写 Event（logged, needs_action=false）──► 客流等统计
  │                不通知、不指派、不 VLM
  └─ alert ──► 策略关卡
                ├─ 未过关：不写行（sustained 计时中）或写 logged 事实（consecutive）
                └─ 过关：待办（needs_action=true, open）
                      ├─ 已有未结待办 → 折叠（repeat），不推送
                      └─ 新建 → 路由员工 + 推个人渠道 + 群机器人兜底
                            ├─ 判定：属实 / 误报 / 看不清
                            ├─ 处置：待处理 → 已确认 → 已结案（误报直接 ignored）
                            └─ 误报可进训练反馈；结案后进 ops 统计
```

---

## 数据模型

### Rule

新增列：

- `intent`：`String(16)`，非空，默认见下。
- `escalate`：`JSON`，默认 `{}`。空对象 = 告警立即升格 + 折叠开启。

默认 intent（创建未传、以及存量回填）：

| 规则类型 | intent |
|---|---|
| `line_crossing` | `observe` |
| 其余 | `alert` |

`escalate` 形状（未知键忽略）：

```json
{
  "mode": "immediate",
  "fold_open": true,
  "consecutive": {"count": 3, "window_sec": 600},
  "sustained": {"duration_sec": 120},
  "compound": {"metric": "footfall_in_today", "op": "gte", "value": 200}
}
```

- `mode`：`immediate` | `sustained` | `consecutive`。缺省 `immediate`。
- `fold_open`：缺省 `true`。
- `consecutive` / `sustained`：仅当 `mode` 对应时读取。
- `compound`：缺省无。`metric` 只允许 `footfall_in_today`；`op` 只允许 `gte`；`value` 为非负整数。

预设（方案包与规则创建 UI 的初始值，用户可改）：

| 类型 | mode | 说明 |
|---|---|---|
| `zone_intrusion` / `loitering` | `immediate` + 折叠 | 一次就要人去；徘徊的「停满 N 秒」仍在检测层 |
| `zone_count` / `object_count` | `sustained` 默认 120 秒 | 避免帧级刷屏 |
| `line_crossing` | 无策略 | observe |

`cooldown`、`active_hours` 含义不变：前者是命中去抖，后者是检测是否启用。

### Event

新增列：

- `intent`：`String(16)`，写入时从规则拷贝，事后改规则不改历史。
- `needs_action`：`Boolean`，默认 `false`，索引。
- `verdict`：`String(16)`，可空。人的判定：`confirmed` | `false_alarm` | `unclear`。与 `vlm_verdict` 并存；展示与结案以人的 `verdict` 为准，无人判定时 UI 可显示 VLM 结果但不把它当成已判定。
- `assignee_id`：可空，`ForeignKey("people.id")`。
- `repeat_count`：`Integer`，默认 1。折叠时递增。

`status` 增加 `logged`（已记录）。`EVENT_STATUSES` 变为 `open | acked | resolved | ignored | logged`。

`assignee` 字符串保留一版：有 `assignee_id` 时与员工 `name` 双写，旧客户端仍能读到名字。禁止再接受任意自由文本指派（PATCH 只接受 `assignee_id`；若仍传 `assignee` 字符串则 400，说明改用员工 id）。

写入规则：

1. `observe` → `needs_action=false`，`status=logged`，不提交 VLM、不提交通知、不跑路由。
2. `alert` + 策略未过关：
   - `sustained`：内存计时，**不写 Event**。
   - `consecutive`：写 `logged` 事实（`needs_action=false`），仍走 `cooldown`；不满 K 次、或 compound 未过关，都不通知。
   - `immediate` + compound 失败：不写行，避免一次入侵因客流不够就落一堆 logged。
3. `alert` 过关且可折叠到未结待办（同 `camera_id` + `rule_id`，`needs_action=true`，`status in (open, acked)`）→ 不新建；`repeat_count += 1`；**首张快照** `snapshot_path` 不变，最新一张写入 `detail["last_snapshot_path"]`；`EventAction.action=repeat`。不通知、不改 `verdict`。
4. 否则新建待办：`needs_action=true`，`status=open`，提交 VLM 与通知/路由。`consecutive` 把已有 logged 行升格时，在升格这一刻才提交 VLM/通知（写入当时不提交）。

`immediate` + 无 compound：行为接近今天，但默认折叠。

### Person / 渠道 / 路由

`people`：

- `id`、`name`（非空，`String(64)`）
- `login_name`：可空，唯一。本期不验证登录，只占位。
- `created_at`

不存密码。没有 `login_name` 的行仍是合法员工。

`person_channels`：

- `person_id`、`kind`（`feishu` | `dingtalk` | `wecom`）、`webhook`、`enabled`
- `kind` 只影响展示与测试文案；发送仍是 HTTP POST JSON（与现有 `send_webhook` 相同 payload）。

`event_routings`：

- `person_id`、`camera_id` 可空、`rule_type` 可空、`enabled`
- 空 = 通配，匹配算法与今天 `match_channels` 相同。

`NotifyChannel` 表与 API **保留**：群机器人，不绑人。新建待办时：先按 routing 通知员工个人渠道，再按现有逻辑匹配群渠道。两者独立，可同时推。

### EventAction

现有 `action` 集合加上：

- `repeat`：payload `{"count": N}`
- `verdict`：payload `{"from": ..., "to": ...}`
- `assign`：payload 改为 `{"from": id|null, "to": id|null}`（兼容读旧的字符串 `from`/`to`）

`actor` 仍为 `local` / `agent` / 渠道名；本期不把操作者换成员工登录身份。

---

## 策略关卡（切片 2）

独立模块 `opencam/detection/escalate.py`，纯函数 + 可注入时钟；`rules.py` 只负责检测。`pipeline._tick` 在 `evaluate` 之后调用升格，再决定写库 / 折叠 / 通知。

顺序：

1. `intent == observe` → 写 logged，结束。
2. compound（若配置）：未过关则**不得打开待办**（也不得折叠进待办）。`consecutive` 仍可写 logged 事实；`immediate` / `sustained` 未过关不写行。该摄像头没有 observe 越线规则时进店数为 0，复合条件不会过关（避免「没客流数据却当客流够了」）。进店数算法：当日本地 0 点至今、`type=line_crossing` 且 `intent=observe` 且 `detail.direction=="in"`，`detail.count` 若 >1 则按 count 累加（与 footfall 一致）。
3. `mode=immediate`：过关。
4. `mode=sustained`：规则引擎侧状态「条件连续为真」的起始时间；满 `duration_sec` 才过关，然后重置计时，避免立刻再开第二条。未满不写库。`RuleEngine` 为 count 类规则增加与 loitering 类似的持续计时，或由 escalate 持有 `rule_id → since`（pipeline 每 tick 都看当前是否仍命中）。推荐：escalate 持有内存状态，tick 上「本帧有该规则的检测命中」视为条件为真，否则为假并清零。过关写 **一条** 待办。
5. `mode=consecutive`：每次检测命中先按 cooldown 写 logged；再数窗口内该 `rule_id` 的事件数（含刚写入的），≥ K 则把 **最后这一条** 升格为待办（改 `needs_action`、`status=open`），不另插一行。窗口内已有未结待办则走折叠，不升格第二条。
6. 折叠：过关后查未结待办，有则 repeat。

`loitering` 的 duration 仍在检测层；其 escalate 默认 `immediate`（检测命中已经是「停够了」）。

---

## API

### 规则

`RuleCreate` / 更新增加可选 `intent`、`escalate`。非法 `intent` 或 `escalate.mode` / `compound.metric` → 400。不传 `intent` 用类型默认。

### 事件

- `GET /events` 增加 `needs_action`（bool，可选）。**不传则返回全部**（CLI/Agent 兼容）。Web 待办箱显式传 `true`。
- `GET /events` 增加 `verdict` 过滤。
- `EventOut` 增加 `intent`、`needs_action`、`verdict`、`assignee_id`、`repeat_count`。
- `EventUpdate`：
  - 增加 `verdict`、`assignee_id`。
  - `status=logged` 不允许经 PATCH 写入。
  - `status=resolved` 仅当当前 `verdict==confirmed`（或本次 PATCH 同时带 `verdict=confirmed`），否则 400：「属实后才能结案」。
  - 设置 `verdict=false_alarm` 时：`status` 自动变为 `ignored`，`acked=true`；记 `verdict` + `status` 两条 action。
  - 设置 `verdict=confirmed` 且当前为 `open`：`status` 自动变为 `acked`（仍可再点结案）。
  - `verdict=unclear` 不自动改 status。
- 观察事件（`needs_action=false`）的 PATCH 若改 status/verdict/assignee → 400：「观察记录不可处置」。

### 员工 / 路由 / 渠道

新前缀（挂 `main.py`）：

- `GET/POST /api/people`，`GET/PATCH/DELETE /api/people/{id}`
- `GET/POST /api/people/{id}/channels`，`PATCH/DELETE /api/people/{id}/channels/{cid}`，`POST .../test`（复用现有测试 payload）
- `GET/POST /api/event-routings`，`PATCH/DELETE /api/event-routings/{id}`

删除员工：有待办 `assignee_id` 指向时，将 `assignee_id` 置空、保留 `assignee` 名字副本，不级联删事件。渠道与路由级联删。

群渠道 `/api/notify-channels` 保持不变。

### 统计

- `GET /api/stats/footfall`：只统计 `type=line_crossing` **且** `intent=observe`。存量回填后旧越线事件带 observe，数字与今天一致。
- `GET /api/stats/ops`：查询 `camera_id` 可选、`date` 可选（同 footfall 的本地日）。返回：

```json
{
  "date": "2026-08-19",
  "camera_id": null,
  "todos": {"opened": 0, "open": 0, "acked": 0, "resolved": 0, "ignored": 0},
  "verdicts": {"confirmed": 0, "false_alarm": 0, "unclear": 0, "none": 0},
  "avg_ack_sec": null,
  "avg_resolve_sec": null
}
```

`opened` = 当日新建待办数（`needs_action=true` 且 `ts` 在当天）。`open` 等为当日新建中当前状态计数。时长：用该事件 `EventAction` 里第一次 `status→acked` / `→resolved` 相对 `Event.ts`；无则该条不进入平均。折叠不另计 opened。

---

## 运行时

`pipeline._tick`：单帧异常仍兜底。升格与写库在同一 session。通知与 VLM 仅对待办的**新建**（折叠不入队）。

`notify.py`：

- `notify_event` 先加载事件，`needs_action` 为假则返回 0。
- 匹配 `event_routings` → 启用中的 `person_channels`；再匹配 `NotifyChannel`。
- 多员工：全部推送；`assignee_id` 取 routing `id` 最小的那个人。无人匹配则不指派，仍推群渠道。
- 单渠道失败不影响其他，逐条 `EventAction.notify`。
- payload 增加 `assignee_id`、`assignee`、`needs_action`、`intent`、`repeat_count`。仍不发快照字节。

重发 `POST /events/{id}/notify`：仅当 `needs_action=true`，否则 400。

---

## Web

- 事件页标题改为「待办」；默认请求 `needs_action=true`。开关「含观察记录」则去掉该参数或传 `false` 的并列视图（观察列表不显示处置按钮）。
- 详情：判定三按钮（属实 / 误报 / 看不清）与处置按钮分开。「处置完成」在未属实时禁用或点了报错。
- 负责人改为员工下拉（`assignee_id`），不再是文本框。
- 规则表单：意图选择；告警时出现升格方式 + 可选复合客流阈值。
- 设置页：保留群机器人；新增「员工」：姓名、可选登录名、个人 webhook、路由（摄像头×类型）。
- Dashboard 客流图继续打 footfall，后端已按 intent 过滤即可。

中文文案：`logged` 展示为「已记录」；`needs_action` 不直接对人展示。

## CLI / Skill

- `opencam events list` 增加 `--needs-action`（true/false，不传=全部）。
- Skill 工作流「查未确认告警」改为待办：`needs_action=true` 且 `status=open`（或现有 `--acked false` 与 `needs_action` 同时说明）。不为此做 Go CLI。

---

## 迁移

Alembic 新版本（在现有链之后），幂等：

1. `rules.intent` / `rules.escalate`；存量 `line_crossing` → observe，其余 → alert；`escalate={}`。
2. `events` 新列；存量 `line_crossing` → `intent=observe`、`needs_action=false`、`status=logged`；其余 → `intent=alert`、`needs_action=true`（保持原 status，已 ignored/resolved 的仍为待办历史，不改成 logged）。
3. 建 `people` / `person_channels` / `event_routings`。
4. `verify_schema` 把新表/列纳入质检。

升级前备份走现有 `ensure_schema`，不手写运行期 ALTER。

---

## 测试

一律 `tmp_settings` + mock detector，不打外网。

| 切片 | 覆盖 |
|---|---|
| 1 | 默认 intent；observe 落 logged 且不 notify/VLM；列表 `needs_action`；Web 默认待办；存量 line_crossing 回填 |
| 2 | sustained 未满不写库、满了写一条；consecutive 第 K 次升格；fold 不新建；compound 客流不足不升格、足够则升格；注入时钟 |
| 3 | 路由通配；多人只指派 id 最小；个人渠道失败不影响群渠道；误报→ignored；未属实 resolved→400；观察 PATCH 处置→400；删员工断开 assignee_id |
| 4 | footfall 忽略 alert 的越线；ops 计数与平均时长 |

`tests/test_upgrade.py` 增加存量库接入样板。改 API 后 `uv run python scripts/export_openapi.py`。

e2e（`test_pipeline_e2e.py`）断言：告警规则产生 `needs_action=true`；若夹具里有越线观察规则，那些事件 `needs_action=false`。

## 错误处理

- 无匹配员工：待办照建，不 500；时间线不写空 assign，只是 `assignee_id` 为空。
- webhook 失败：记 action，主链路继续。
- 折叠目标已 `resolved`/`ignored`：视为无折叠目标，新建待办。
- `escalate` JSON 损坏：按 `immediate` + `fold_open=true` 处理并打 warning，不杀线程。
- 复合条件查客流失败：当关卡失败（不升格），记日志。

## 文档

README / AGENTS.md / OpenAPI 描述同步：事件分观察与待办；通知含员工渠道。不把「数据不出本机」当卖点文案（既有约定）。

---

## 验收

1. 门口越线只进客流图与观察记录，不出现在默认待办箱，不推 webhook。
2. 后厨闯入立即出待办；同一规则未结案时再闯入只增加 `repeat_count`，不第二条待办、不第二次推送。
3. 排队规则可配「持续 2 分钟」才出一条待办；可配「当日进店 ≥ N」才允许升格。
4. 员工可无登录名；路由命中后 `assignee_id` 有值，飞书 webhook 收到 payload；员工不登录也能当负责人。
5. 误报关闭为 `ignored` 且 `verdict=false_alarm`；未属实不能 `resolved`。
6. `GET /api/stats/ops` 返回当日待办与判定计数；`make test` 全绿。
