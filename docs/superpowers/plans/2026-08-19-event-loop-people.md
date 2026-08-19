# 事件闭环：观察 / 待办 / 员工通知 Implementation Plan

> **For agentic workers:** 验收只对照本计划与规格 `docs/superpowers/specs/2026-08-19-event-loop-people-design.md`。从 `origin/main` 开分支。PR 标题和正文必须含对应 Multica issue key（如 `CAM-xx` / `Closes CAM-xx`）。REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（或 executing-plans）按 task 执行。

**Goal:** 检测命中先记事实；只有升格后的待办才叫人处理、才推 IM；客流等观察记录不进默认待办箱。

**Architecture:** `Rule.intent` 决定观察还是告警；`opencam/detection/escalate.py` 决定告警要不要变成待办（含折叠）；`people` + `event_routings` + `person_channels` 只作用在 `needs_action=true` 的新建待办上。群机器人 `NotifyChannel` 若本分支尚无则在 Stage 3 一并引入，不把旧渠道自动改成员工。

**Tech Stack:** FastAPI + SQLAlchemy SQLite、Pydantic v2、httpx 测 webhook、无构建原生 Web、pytest TestClient + mock detector。

## Global Constraints

- 规格原文：`docs/superpowers/specs/2026-08-19-event-loop-people-design.md`（字段名、HTTP 码、中文错误文案以规格为准）。
- 基线：**`origin/main`**。该基线没有 `Event.status` / `EventAction` / `NotifyChannel` / Alembic / `opencam/notify.py`。本计划按这个事实写，不要假设工作区未提交的处置闭环已经在 main 上。
- **禁止**把工作区未提交的 `opencam/migrations/**`、`doctor.py`、`docs/upgrade-safety.md` 整包拷进本 PR（会和本地 WIP 对撞）。schema 用基线已有的 `opencam/db.py` `_migrate`：缺列 `ALTER TABLE` + 回填。若开分支时 `opencam/migrations/` **已经在 origin/main**，则改为追加一条幂等 Alembic revision，不要再扩 `_migrate`。
- Python ≥ 3.12；`from __future__ import annotations`；用户可见文案中文，标识符英文。
- CLI 只能 import 轻量依赖，禁止 import ultralytics/torch。
- 测试：`tmp_settings` + `OPENCAM_DETECTOR=mock`，不下载模型、不打真实飞书/钉钉（webhook 用 `httpx.MockTransport` 或本地 httpx mock）。
- 改 API 后 `uv run python scripts/export_openapi.py`。
- 命令：`uv run pytest`；单测示例见各 Task。
- 出站 payload 只含事件元数据，不含快照字节。

## 范围边界

做：规格四个切片（意图与待办箱、策略关卡、员工/判定/通知、经营统计）。  
不做：登录鉴权、IM 按钮回写、排班/岗位、任意指标表达式、把 `NotifyChannel` 迁成员工、另建时序库、完整 upgrade-safety Alembic 栈（除非 main 已有）。

规格写「走 Alembic」；本计划在 main 尚无迁移栈时改用 `_migrate` ALTER，这是对基线的有意偏离，验收以本计划为准。

## 验收标准（DoD）

与规格「验收」一致：

1. 门口越线（`line_crossing` 默认 observe）只进客流/观察记录，默认待办箱看不到，不提交 VLM、不推 webhook。
2. 后厨闯入立即出待办；同一 `camera_id+rule_id` 未结案时再闯入只增加 `repeat_count`，不第二条待办、不第二次推送。
3. 排队规则可配 `sustained`（持续 N 秒才一条待办）和 `compound.footfall_in_today >= N`。
4. 员工可无 `login_name`；路由命中后 `assignee_id` 有值；个人 webhook 收到 JSON；不登录也能当负责人。
5. `verdict=false_alarm` → `status=ignored`；未 `confirmed` 时 `resolved` 返回 400「属实后才能结案」。
6. `GET /api/stats/ops` 返回当日待办与判定计数；`GET /api/stats/footfall` 忽略 `intent=alert` 的越线；`uv run pytest` 全绿。

## 拆解思路（子 Issue / stage）

| Stage | 子任务 | 可独立验收 |
|---|---|---|
| 1 | 规则 intent + 事件 needs_action + 待办箱 | `tests/test_events_api.py` 新用例 + e2e 入侵为待办 |
| 2 | 策略关卡：immediate / sustained / consecutive / compound + 折叠 | `tests/test_escalate.py` 全绿 |
| 3 | 员工、路由、个人 IM、判定与处置拆开 | `tests/test_people_api.py` + `tests/test_notify.py` + 事件 PATCH 判定 |
| 4 | ops 统计 + footfall 按 intent + CLI/Skill/文档 | `tests/test_stats_api.py` 新用例 + openapi |

后一 stage 依赖前一 stage 已合入（或基于前一 PR 分支）。

## 文件地图

- Create: `opencam/detection/escalate.py`、`opencam/notify.py`、`opencam/api/people.py`、`opencam/api/notify.py`、`tests/test_escalate.py`、`tests/test_people_api.py`、`tests/test_notify.py`
- Modify: `opencam/models.py`、`opencam/db.py`（仅 `_migrate` 补列）、`opencam/pipeline.py`、`opencam/api/events.py`、`opencam/api/rules.py`、`opencam/api/stats.py`、`opencam/main.py`、`opencam/cli.py`、`opencam/web/pages/events.js`、`opencam/web/pages/rules.js`、`opencam/web/pages/settings.js`、`opencam/web/index.html`（侧栏「事件」可改文案为「待办」，可选）、`skills/opencam/SKILL.md`、`tests/test_events_api.py`、`tests/test_pipeline_e2e.py`、`tests/test_cli.py`、`tests/test_web.py`、`tests/test_skill_contract.py`、`README.md`、`AGENTS.md`、`docs/openapi.json`

共享函数（后任务只准用这些名字，不要另起炉灶）：

```python
# opencam/models.py
INTENT_OBSERVE = "observe"
INTENT_ALERT = "alert"
EVENT_LOGGED = "logged"
EVENT_OPEN = "open"
EVENT_ACKED = "acked"
EVENT_RESOLVED = "resolved"
EVENT_IGNORED = "ignored"

def default_intent(rule_type: str) -> str:
    return INTENT_OBSERVE if rule_type == "line_crossing" else INTENT_ALERT

# opencam/pipeline.py（Stage 1 抽出，Stage 2 接 escalate）
def persist_hit(session, camera_id: int, rule, hit, snapshot_path: str | None) -> "Event | None":
    """按 intent/策略写库或折叠。返回新建或升格后的待办 Event；观察/未过关可返回 logged 或 None。"""
```

---

### Task 1: 规则意图 + 待办箱

**Files:**
- Modify: `opencam/models.py`（`Rule.intent`/`escalate`，`Event.intent`/`needs_action`/`status`/`repeat_count`，常量与 `default_intent`，`RuleCreate`/`EventOut`）
- Modify: `opencam/db.py` `_migrate` 补列并回填
- Modify: `opencam/pipeline.py`（`persist_hit` + 观察不 submit VLM）
- Modify: `opencam/api/rules.py`、`opencam/api/events.py`
- Modify: `opencam/web/pages/events.js`
- Test: `tests/test_events_api.py`、`tests/test_pipeline_e2e.py`、`tests/test_web.py`
- 本 Task 结束时 `uv run python scripts/export_openapi.py`

**Interfaces:**
- Consumes: 现有 `Rule`/`Event`、`pipeline._tick`、`GET /events`
- Produces: `default_intent`；`persist_hit`；`GET /events?needs_action=`；规则 CRUD 读写 `intent`/`escalate`；观察事件 `status=logged`、`needs_action=false`

Stage 1 **不做** 折叠、sustained、员工、判定 `verdict`、webhook。`escalate` 列存 JSON，默认 `{}`，本阶段不解释（告警一律立即当待办）。不要给 `zone_count` 预填 `sustained`，否则 Stage 2 落地前超员规则会不再出待办。

- [ ] **Step 1: 写失败测试**

在 `tests/test_events_api.py` 追加：

```python
def test_rule_default_intent_line_crossing_observe(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "line_crossing",
        "params": {"line": [[0, 120], [320, 120]], "direction": "both"},
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["intent"] == "observe"
    assert resp.json()["escalate"] == {}


def test_rule_default_intent_intrusion_alert(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "zone_intrusion",
        "params": {"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    })
    assert resp.status_code == 201, resp.text
    assert resp.json()["intent"] == "alert"


def test_rule_rejects_bad_intent(client):
    camera_id = _make_camera(client)
    resp = client.post(f"/cameras/{camera_id}/rules", json={
        "type": "zone_intrusion",
        "intent": "banana",
        "params": {"polygon": [[0, 0], [10, 0], [10, 10], [0, 10]]},
    })
    assert resp.status_code == 422 or resp.status_code == 400


def test_events_needs_action_filter(client):
    camera_id = _make_camera(client)
    session = get_session()
    try:
        todo = Event(
            camera_id=camera_id, type="zone_intrusion", confidence=0.9,
            intent="alert", needs_action=True, status="open", detail={})
        obs = Event(
            camera_id=camera_id, type="line_crossing", confidence=0.9,
            intent="observe", needs_action=False, status="logged", detail={})
        session.add_all([todo, obs])
        session.commit()
        todo_id, obs_id = todo.id, obs.id
    finally:
        session.close()
    all_rows = client.get("/events").json()
    ids = {e["id"] for e in all_rows}
    assert todo_id in ids and obs_id in ids
    only_todo = client.get("/events", params={"needs_action": True}).json()
    assert {e["id"] for e in only_todo} == {todo_id}
    only_obs = client.get("/events", params={"needs_action": False}).json()
    assert {e["id"] for e in only_obs} == {obs_id}
```

在 `tests/test_pipeline_e2e.py` 的入侵 e2e 末尾增加：

```python
    assert events[0].intent == "alert"
    assert events[0].needs_action is True
    assert events[0].status == "open"
```

另写一个 e2e 或同文件新测试：摄像头只挂 `line_crossing`，产出事件 `intent=="observe"`、`needs_action is False`、`status=="logged"`。mock detector 仍返回框即可，越线规则用一条横穿画面的线。

`tests/test_web.py`：

```python
def test_events_page_defaults_to_todos(client):
    js = client.get("/static/pages/events.js").text
    assert "needs_action" in js
    assert "待办" in js
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_events_api.py::test_rule_default_intent_line_crossing_observe tests/test_events_api.py::test_events_needs_action_filter -v
```

Expected: FAIL（`intent` 字段不存在或 500）

- [ ] **Step 3: 最小实现**

`models.py`：

- 增加常量 `INTENT_*`、`EVENT_LOGGED`（以及 `EVENT_OPEN/ACKED/RESOLVED/IGNORED`，即使本阶段 Web 还只用 open/logged/acked）。
- `Rule.intent: Mapped[str] = mapped_column(String(16), default=INTENT_ALERT)`
- `Rule.escalate: Mapped[dict] = mapped_column(JSON, default=dict)`
- `Event.intent`、`needs_action`（Boolean，index）、`status`（默认 `EVENT_OPEN`）、`repeat_count`（默认 1）
- `def default_intent(rule_type: str) -> str`
- `RuleCreate.intent` 可选，`pattern="^(observe|alert)$"`；`escalate` 可选 dict，默认 `{}`
- 创建规则：`intent = body.intent or default_intent(body.type)`；`escalate = body.escalate or {}`
- `EventOut` 增加四字段

`db.py` `_migrate`：对 `rules`/`events` `inspect` 缺列则 ALTER；然后

```sql
UPDATE rules SET intent='observe' WHERE type='line_crossing' AND (intent IS NULL OR intent='');
UPDATE rules SET intent='alert' WHERE intent IS NULL OR intent='';
UPDATE events SET intent='observe', needs_action=0, status='logged' WHERE type='line_crossing';
UPDATE events SET intent='alert', needs_action=1 WHERE type!='line_crossing' AND (intent IS NULL OR intent='');
```

存量非越线事件：`needs_action=1`，`status` 若仍空则按 `acked` 列：`acked=1` → `acked`，否则 `open`。

`pipeline.py` 抽出 `persist_hit`：从 `rule.intent or default_intent(rule.type)` 拷到事件；observe → `needs_action=False`、`status=EVENT_LOGGED`；alert → `needs_action=True`、`status=EVENT_OPEN`。`_tick` 仅当 `event is not None and event.needs_action` 时 `vlm_reviewer.submit(event.id)`。

`GET /events` 增加 `needs_action: Optional[bool] = Query(None)`，传入才过滤。

`events.js`：标题「待办」；默认 `params.set('needs_action', 'true')`；增加 checkbox「含观察记录」，勾选后去掉该参数。观察行不显示 ack 按钮（`needs_action===false`）。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_events_api.py tests/test_pipeline_e2e.py tests/test_web.py -v
```

Expected: PASS。然后 `uv run python scripts/export_openapi.py`。

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: 规则意图与待办箱（observe 不进默认队列）"
```

---

### Task 2: 策略关卡与折叠

**Files:**
- Create: `opencam/detection/escalate.py`、`tests/test_escalate.py`
- Modify: `opencam/pipeline.py`（`persist_hit` 调用 escalate）
- Modify: `opencam/models.py` / `opencam/api/rules.py`（校验 `escalate.mode` / `compound`）
- Modify: `opencam/web/pages/rules.js`（告警规则出现升格方式；可选复合客流阈值）

**Interfaces:**
- Consumes: Task 1 的 `persist_hit`、`INTENT_*`、`Event` 事实行
- Produces:

```python
# opencam/detection/escalate.py
from dataclasses import dataclass

@dataclass
class EscalateDecision:
    write_logged: bool          # consecutive 未满 K：写 logged
    open_todo: bool             # 打开或升格待办
    fold: bool                  # 并入已有未结待办

class Escalator:
    def __init__(self, clock=time.time): ...
    def decide(self, session, rule, camera_id: int, now: float | None = None) -> EscalateDecision:
        """compound → mode → fold。escalate JSON 损坏时 warning 并视为 immediate+fold_open。"""
    def note_hit(self, rule_id: int, now: float) -> None:
        """本帧该规则检测命中，供 sustained 计时。"""
    def note_miss(self, rule_id: int) -> None:
        """本帧该规则未命中，sustained 清零。"""
```

进店数：该摄像头当天本地 0 点至今、`type=='line_crossing'` 且 `intent=='observe'`，对每条 `detail.count`（缺省 1）在 `detail.direction=='in'` 或 `detail.crossings[]` 里累加 in（与 `stats.footfall` 同一套 crossing 展开）。无观察越线则 0，compound 不过关。

折叠目标：同 `camera_id`+`rule_id`，`needs_action=True`，`status in ('open','acked')`。命中则 `repeat_count += 1`，`detail['last_snapshot_path']=新快照`，不改 `snapshot_path`，加 `EventAction(action='repeat', payload={'count': N})`。无 `EventAction` 表时（Stage 1 若未建）：先在本 Task 用 `_migrate` 建 `event_actions`（列与规格一致：event_id/action/actor/payload/ts）。**本 Task 必须建 `event_actions`**，折叠与后续通知都要写时间线。

`persist_hit` 伪代码：

```python
intent = rule.intent or default_intent(rule.type)
if intent == INTENT_OBSERVE:
    return _insert(session, ..., needs_action=False, status=EVENT_LOGGED, intent=intent)

escalator.note_hit(rule.id, now)
decision = escalator.decide(session, rule, camera_id, now)
if not decision.write_logged and not decision.open_todo:
    return None
event = _insert(... logged...) if decision.write_logged and not decision.open_todo else None
if decision.fold:
    existing = _open_todo(session, camera_id, rule.id)
    _fold(existing, snapshot_path)
    return existing
if decision.open_todo:
    if event is None:
        event = _insert(..., needs_action=True, status=EVENT_OPEN)
    else:
        event.needs_action = True
        event.status = EVENT_OPEN
    return event
return event
```

`_tick`：对本摄像头每条 enabled 规则，若本帧 `evaluate` 没有该 `rule.id` 的 hit，则 `escalator.note_miss(rule.id)`。VLM 仅当返回值是新建/升格待办且本 tick **不是 fold**（fold 不 submit）。consecutive 升格最后一条时在升格这一刻 submit。

非法 `escalate.mode` 或 `compound.metric != footfall_in_today` 或 `op != gte`：规则 POST/PUT 400，detail 含「escalate」。

- [ ] **Step 1: 写失败测试**（`tests/test_escalate.py`）

用注入时钟的 `Escalator` + 真实 sqlite（`tmp_settings`）插入 Rule/Event，不要起 CaptureWorker。

```python
def test_sustained_does_not_write_until_duration(session, rule_zone_count, escalator, clock):
    escalator.note_hit(rule_zone_count.id, clock.t)
    d = escalator.decide(session, rule_zone_count, camera_id=1, now=clock.t)
    assert d.open_todo is False and d.write_logged is False
    clock.t += 119
    escalator.note_hit(rule_zone_count.id, clock.t)
    d = escalator.decide(session, rule_zone_count, camera_id=1, now=clock.t)
    assert d.open_todo is False
    clock.t += 1
    escalator.note_hit(rule_zone_count.id, clock.t)
    d = escalator.decide(session, rule_zone_count, camera_id=1, now=clock.t)
    assert d.open_todo is True
```

`zone_count` 测试规则：`intent=alert`，`escalate={"mode":"sustained","fold_open":True,"sustained":{"duration_sec":120}}`。

再写：

- `test_consecutive_promotes_kth_event`：mode consecutive count=3 window=600；前两次 `write_logged`；第三次 `open_todo`。
- `test_fold_does_not_create_second_todo`：已有 open 待办时 `fold=True`。
- `test_compound_blocks_todo_without_footfall`：immediate + compound value=200，库里无 observe 越线 → `open_todo=False`；插入足够 in 越线后过关。
- `test_resolved_todo_is_not_fold_target`：已 resolved 的不当折叠目标。
- `test_bad_escalate_json_treated_as_immediate`：`escalate={"mode":"nope"}` 存库后 decide 不抛异常，视为 immediate。

另在 `test_pipeline_e2e.py` 或 `test_escalate.py` 用 `persist_hit` 断言 fold 后 `repeat_count==2` 且只有一行 `needs_action=True`。

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_escalate.py -v
```

Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 escalate + persist_hit 接线 + rules.js**

规则表单：`intent` 下拉；`intent=alert` 时显示 mode 选择（立即/持续/连续）和「当日进店 ≥」数字空=不配 compound。提交时组 `escalate` 对象。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_escalate.py tests/test_events_api.py tests/test_pipeline_e2e.py -v
```

Expected: PASS。导出 openapi。

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: 告警策略关卡与待办折叠"
```

---

### Task 3: 员工、路由、IM、判定

**Files:**
- Create: `opencam/api/people.py`、`opencam/notify.py`、`opencam/api/notify.py`（群机器人，若 Task 1/2 未建）、`tests/test_people_api.py`、`tests/test_notify.py`
- Modify: `opencam/models.py`（`Person`/`PersonChannel`/`EventRouting`/`NotifyChannel`、`Event.verdict`/`assignee_id`、`EventUpdate`）、`opencam/db.py` `_migrate` 建表补列、`opencam/main.py` 挂路由、`opencam/api/events.py` PATCH 判定规则、`opencam/pipeline.py`（新建待办才 `notifier.submit`）、`opencam/web/pages/settings.js`、`opencam/web/pages/events.js`

**Interfaces:**
- Produces:
  - ORM `Person(id, name, login_name unique nullable, created_at)`
  - `PersonChannel(id, person_id, kind, webhook, enabled)`，`kind` 属于 `feishu|dingtalk|wecom`
  - `EventRouting(id, person_id, camera_id nullable, rule_type nullable, enabled)`
  - `NotifyChannel` 与规格一致（群机器人）
  - 路由：`GET/POST /api/people`、`GET/PATCH/DELETE /api/people/{id}`、渠道 CRUD+`POST .../test`、`GET/POST /api/event-routings`、`PATCH/DELETE /api/event-routings/{id}`、`GET/POST /api/notify-channels` 及 patch/delete/test（路径以规格为准）
  - `notify_event(event_id) -> int`：`needs_action` 为假返回 0；先个人渠道再群渠道；多人全推，`assignee_id` = 匹配 routing 的最小 `id` 对应 `person_id`；双写 `Event.assignee=person.name`
  - 删除员工：渠道与路由级联删；事件 `assignee_id` 置空，保留 `assignee` 名字

PATCH `/events/{id}`（本 Task 引入完整 `EventUpdate`，origin/main 若只有 POST ack 则一并加 PATCH）：

- 观察事件改 `status`/`verdict`/`assignee_id` → 400「观察记录不可处置」
- `status=logged` → 400
- `status=resolved` 且（当前或本次）`verdict != confirmed` → 400「属实后才能结案」
- `verdict=false_alarm` → `status=ignored`，`acked=True`，两条 EventAction
- `verdict=confirmed` 且 `status=open` → `status=acked`
- 只传 `assignee` 字符串 → 400，说明改用 `assignee_id`
- `POST /events/{id}/notify`：`needs_action=false` → 400

payload 字段：规格 `event_payload` + `assignee_id`/`needs_action`/`intent`/`repeat_count`。测试用 `httpx.MockTransport` 收 POST。

设置页：保留/新增群机器人表；新增员工（姓名、可选登录名、渠道 webhook、路由摄像头×类型）。事件页：判定三按钮；负责人下拉 `GET /api/people`；未属实时禁用「处置完成」。

- [ ] **Step 1: 写失败测试**

`tests/test_people_api.py`：创建无 login_name 的员工 201；routing 通配匹配；删员工后事件 `assignee_id is None` 且 `assignee` 仍为原名。

`tests/test_notify.py`：建待办 + 员工渠道（MockTransport 200）+ 群渠道；`notify_event` 两次 POST；`assignee_id` 为 routing id 较小者。个人渠道 500 时群渠道仍 200，两条 `EventAction.notify`。`needs_action=False` 的事件 `notify_event` 返回 0 且无 HTTP。fold 不第二次 POST（pipeline 层测或 notify 不在 fold 路径调用）。

`tests/test_events_api.py`：误报 PATCH → ignored + false_alarm；未属实 resolved → 400；观察 PATCH status → 400。

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_people_api.py tests/test_notify.py -v
```

Expected: FAIL

- [ ] **Step 3: 实现模型、API、Notifier 线程（仿 `vlm_reviewer`：daemon + queue + 循环兜底）、Web**

`NotifyChannel` 匹配逻辑与规格相同：`camera_id`/`rule_type` 空通配。不要把群渠道自动插入 `people`。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_people_api.py tests/test_notify.py tests/test_events_api.py tests/test_escalate.py -v
```

Expected: PASS。导出 openapi。

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: 员工路由与待办判定，IM 只推新建待办"
```

---

### Task 4: 经营统计与 CLI/文档

**Files:**
- Modify: `opencam/api/stats.py`（footfall 加 `intent==observe`；新增 `GET /api/stats/ops`）
- Modify: `opencam/cli.py`（`events list --needs-action`；`stats ops`）
- Modify: `skills/opencam/SKILL.md`（查未确认改为待办：`--needs-action true` 且未 ack/open）
- Modify: `tests/test_cli.py`、`tests/test_skill_contract.py`、`README.md`、`AGENTS.md`
- Test: 扩展现有 stats 测试文件；若无则 Create `tests/test_stats_api.py`

**Interfaces:**
- Consumes: Task 1–3 的 `intent`/`needs_action`/`verdict`/`EventAction`
- Produces: 规格中的 ops JSON。`opened` = 当天 `needs_action=true` 的行（按 `Event.ts`）。状态桶是这些行的当前 status。`avg_ack_sec` / `avg_resolve_sec`：该事件第一条 `action=status` 且 `payload.to` 为 `acked`/`resolved` 的 `ts - Event.ts`；没有则不计入平均，全无则 `null`。折叠不另计 opened。

- [ ] **Step 1: 写失败测试**

```python
def test_footfall_ignores_alert_line_crossing(client):
    # 插入两条当天 line_crossing：一条 observe in，一条 alert in
    # GET /api/stats/footfall 的 total_in == 1

def test_ops_counts_todos_and_verdicts(client):
    # 当天新建待办 2 条，一条 confirmed+resolved（有对应 EventAction），一条 false_alarm
    # opened==2，verdicts.confirmed==1，false_alarm==1
```

CLI：`events list --needs-action true` 请求 query 含 `needs_action=true`（可用现有 `cli_env` mock）。

Skill：`tests/test_skill_contract.py` 断言正文含 `needs-action` 或 `needs_action`，且查告警工作流不再只写 `--acked false` 而不提待办。

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_stats_api.py tests/test_cli.py tests/test_skill_contract.py -v
```

- [ ] **Step 3: 实现过滤、ops、CLI、Skill、README/AGENTS 各一行说明观察 vs 待办**

- [ ] **Step 4: 跑全量测试**

```bash
uv run pytest
uv run python scripts/export_openapi.py
```

Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: 待办经营摘要与客流只统计观察越线"
```

---

## 规格对照（self-review）

| 规格项 | 任务 |
|---|---|
| intent / needs_action / logged / 待办箱 / 存量越线回填 | Task 1 |
| 默认 intent 表、escalate 存盘 | Task 1 存，Task 2 解释 |
| sustained / consecutive / compound / fold / EventAction.repeat | Task 2 |
| 观察不写 sustained 中间帧 | Task 2 |
| people / 渠道 / routing / 群机器人兜底 / 可选 login_name | Task 3 |
| verdict vs status、误报 ignored、resolved 需属实 | Task 3 |
| 新建待办才通知，fold 不推 | Task 3 |
| footfall 仅 observe、ops 接口 | Task 4 |
| CLI `--needs-action`、Skill 待办 | Task 4 |
| 登录鉴权 / IM 回写 / Alembic 全家桶 | 明确不做 |

未纳入本计划的规格句「Alembic 新版本」：见 Global Constraints 的基线偏离。`assignee` 自由文本在 Task 3 起拒绝；Task 1–2 基线若仍无 PATCH 则不涉及。
