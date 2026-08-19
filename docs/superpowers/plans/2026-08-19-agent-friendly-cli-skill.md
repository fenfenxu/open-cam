# Agent 友好的 CLI 与 Skill Implementation Plan

> **For agentic workers:** 验收只对照本计划与规格 `docs/superpowers/specs/2026-08-19-agent-friendly-cli-skill-design.md`。从 `origin/main` 开分支。不要把工作区里未提交的 Alembic / clip / doctor 改动当基线。PR 标题和正文必须含对应 Multica issue key（如 `CAM-xx` / `Closes CAM-xx`）。REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（或 executing-plans）按 task 执行。

**Goal:** 让 Agent 把 `opencam` 当唯一入口时，成功输出永远能 `json.loads`，不知道命令就 `--help`，高频任务走 Skill 工作流而不是过期示例。

**Architecture:** CLI 仍是 REST 薄封装。统一 `_emit` / `_save_bytes` 的 JSON 契约；Skill 改成工作流 + 指向 `--help`；用 `opencam api METHOD PATH` 覆盖 CLI 尚未包到的接口。不改服务端列表分页形状。

**Tech Stack:** argparse + httpx CLI、pytest（现有 `cli_env` / `run_cli`）、无构建 Skill markdown。

## Global Constraints

- 规格：`docs/superpowers/specs/2026-08-19-agent-friendly-cli-skill-design.md`
- 基线：`origin/main`。禁止引入 Alembic；禁止把 clip/doctor 未提交改动带进本 PR。
- CLI（`opencam/cli.py`）只能 import httpx/argparse 等轻量依赖，禁止 import 会加载 ultralytics/torch 的包内模块。
- 测试用 `tmp_settings`，`OPENCAM_DETECTOR=mock`。
- Python ≥ 3.12；`from __future__ import annotations`；用户可见文案中文，标识符英文。
- 视频数据不出本机。本轮不改 REST schema，不必重导 OpenAPI，除非 Task 3 为 `api` 加了新 HTTP 路由（不要加）。

## 范围边界

做：stdout JSON 契约、无参数 help、Skill 重写、Skill 反模式测试、`opencam api`。  
不做：`{items,total}` 列表信封、Go 重写、`--jq`、watch、训练专用子命令、Web 改版。

## 验收标准（DoD）

与规格「验收」一致：delete/snapshot stdout 可 `json.loads`；`opencam` 无参数退出 0；`test_cli.py` + `test_skill_contract.py` 全绿；`opencam api GET /cameras` 与 `cameras list` 同形状；Skill < 400 词且无 `--pretty` / 假多边形 / 斜杠速记。

## 拆解思路（子 Issue / stage）

| Stage | 子任务 | 可独立验收 |
|---|---|---|
| 1 | CLI stdout JSON + 无参数 help | `tests/test_cli.py` 新增用例全绿 |
| 2 | Skill 工作流 + 反模式回归网 | `tests/test_skill_contract.py` 全绿 |
| 3 | `opencam api METHOD PATH` | `test_api_get_cameras_matches_list` 全绿 |

后一 stage 依赖前一 stage 已合入（或基于前一 PR 分支）。Task 2 不依赖 Task 1 的运行时行为，但 Skill 正文必须描述 Task 1 的 JSON 契约，所以仍 stage 顺序。

## 文件地图

- Modify: `opencam/cli.py`、`tests/test_cli.py`、`skills/opencam/SKILL.md`
- Create: `tests/test_skill_contract.py`
- 不改：`opencam/api/*`、`docs/openapi.json`、Web

---

### Task 1: CLI stdout 统一为 JSON

**Files:**
- Modify: `tests/test_cli.py`（改 `test_cameras_delete`，追加 snapshot / rules delete / no-args）
- Modify: `opencam/cli.py`（`_emit`、`_save_bytes`、delete/uninstall/snapshot、`main` 无参数）

**Interfaces:**
- Consumes: 现有 `run_cli`、`cli_env`、`_request`
- Produces: `_emit(None)` → `{"ok": true}`；`_save_bytes(data, path) -> dict`；delete stdout `{"ok": true, "id": N}`

- [ ] **Step 1: 写失败测试**

把 `test_cameras_delete` 改成解析 JSON，并追加：

```python
def test_cameras_delete(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    deleted = run_cli(capsys, "cameras", "delete", "1")
    assert deleted == {"ok": True, "id": 1}
    assert run_cli(capsys, "cameras", "list") == []


def test_rules_delete_stdout_is_json(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    rule = run_cli(capsys, "rules", "create", "1", "--type", "zone_count",
                   "--params", '{"threshold": 5}')
    deleted = run_cli(capsys, "rules", "delete", "1", str(rule["id"]))
    assert deleted == {"ok": True, "id": rule["id"]}


def test_snapshot_stdout_is_json(cli_env, capsys, tmp_path, monkeypatch):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    jpeg = b"\xff\xd8fake"
    real_request = cli._request

    def fake_request(client, method, path, **kwargs):
        if str(path).endswith("snapshot.jpg"):
            return jpeg
        return real_request(client, method, path, **kwargs)

    monkeypatch.setattr(cli, "_request", fake_request)
    dest = tmp_path / "cam.jpg"
    out = run_cli(capsys, "cameras", "snapshot", "1", "-o", str(dest))
    assert out["ok"] is True
    assert out["bytes"] == len(jpeg)
    assert out["path"] == str(dest)
    assert dest.read_bytes() == jpeg


def test_no_args_prints_help(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "cameras" in out
    assert "events" in out
```

packs uninstall、events snapshot 走同一 `_save_bytes` / `_emit`，不必各写一条，但实现时必须改掉 `print(f"方案包…")`。

- [ ] **Step 2: 跑测试确认失败**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_cli.py::test_cameras_delete tests/test_cli.py::test_no_args_prints_help -v`  
Expected: FAIL（`JSONDecodeError` 或退出码 2）

- [ ] **Step 3: 实现**

`_emit` / `_save_bytes`：

```python
def _emit(data: Any, pretty: bool) -> None:
    if data is None:
        data = {"ok": True}
    if pretty:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _save_bytes(data: bytes, path: str) -> dict:
    with open(path, "wb") as f:
        f.write(data)
    return {"ok": True, "path": path, "bytes": len(data)}
```

delete / snapshot / uninstall 全部 `_emit(...)`，禁止 `print("…已删除")`。

`build_parser`：`add_subparsers(dest="resource", required=False)`。`main` 在 `parse_args` 之后：

```python
if not args.resource:
    parser.print_help()
    sys.exit(0)
```

`events list` 给 `--page-size` / `--offset` 写 help（「满页再用 --offset 翻页」）。

- [ ] **Step 4: 跑测试确认通过**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_cli.py -v`  
Expected: PASS（含原有 create/list/ack/footfall）

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: make CLI stdout always JSON for agent parsers

EOF
)"
```

---

### Task 2: Skill 改为工作流 + 反模式回归

**Files:**
- Create: `tests/test_skill_contract.py`
- Modify: `skills/opencam/SKILL.md`（整文件替换）

**Interfaces:**
- Consumes: Task 1 的 JSON 契约与 `--help` 发现路径
- Produces: Skill 正文；pytest 锁住反模式

- [ ] **Step 1: 写失败测试**

创建 `tests/test_skill_contract.py`（完整内容以规格「Skill」节为准，断言如下）：

```python
from pathlib import Path
import re

SKILL = Path(__file__).resolve().parents[1] / "skills/opencam/SKILL.md"

def _text() -> str:
    return SKILL.read_text(encoding="utf-8")

def _bash_fences(text: str) -> str:
    return "\n".join(re.findall(r"```(?:bash|sh)?\n(.*?)```", text, re.S))

def test_description_is_trigger():
    fm = _text().split("---", 2)[1]
    desc = next(line.split(":", 1)[1].strip()
                for line in fm.splitlines() if line.startswith("description:"))
    assert desc.lower().startswith("use when")
    assert "opencam cameras" not in desc

def test_bash_examples_are_copy_pasteable_and_compact():
    bash = _bash_fences(_text())
    assert bash.strip()
    assert "--pretty" not in bash
    assert " / " not in bash
    assert "[[0,0]" not in bash
    assert "opencam_client" not in bash
    assert "curl " not in bash

def test_skill_points_to_help_as_source_of_truth():
    text = _text()
    assert "opencam --help" in text
    assert "opencam events --help" in text or "opencam <resource> --help" in text

def test_skill_teaches_pagination_and_json_stdout():
    text = _text()
    assert "--page-size" in text
    assert "--offset" in text
    assert "json" in text.lower()

def test_skill_teaches_snapshot_before_polygon_rules():
    text = _text()
    assert "cameras snapshot" in text
    assert "像素" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_skill_contract.py -v`  
Expected: FAIL（旧 Skill description 不以 Use when 开头；bash 含 `--pretty`）

- [ ] **Step 3: 重写 `skills/opencam/SKILL.md`**

必须包含：

- frontmatter `description: Use when the user asks about open-cam cameras, unacked alerts or events, footfall stats, industry packs, live snapshots, or detection rules.`
- 默认紧凑 JSON、`json.loads`、删除类 `{"ok":true,"id":...}`、不要 `--pretty`
- `opencam --help` 与 `opencam <resource> --help`
- 工作流「查未确认告警」：`events list --acked false` → 满页 `--offset` → `get` → 说明后再 `ack`
- 工作流「加区域规则」：`cameras get` → `cameras snapshot` → 从该图量像素 → `rules presets` → `rules create`；量不到就问用户，禁止 1080p 经验矩形
- fenced bash 里只有合法单行命令（`cameras list` / `start 1` / `stop 1` / `delete 1` 分行，不要 ` / `）
- 不要 `opencam_client.py`、不要 `GET /events/{id}/clip` 当主路径（片段写「见 `opencam events --help`」）
- 全文目标 < 400 词

- [ ] **Step 4: 跑测试确认通过**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_skill_contract.py tests/test_cli.py -v`  
Expected: PASS；`wc -w skills/opencam/SKILL.md` 小于 400

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
docs: rewrite opencam skill as agent workflows plus help pointers

EOF
)"
```

---

### Task 3: `opencam api` 逃生舱

**Files:**
- Modify: `tests/test_cli.py`
- Modify: `opencam/cli.py`（`_api` handler + `api` 子命令）
- Modify: `skills/opencam/SKILL.md`（发现命令里加一句：CLI 没有的接口用 `opencam api METHOD PATH`）

**Interfaces:**
- Consumes: `_request`、`_emit`、`_save_bytes`
- Produces: `opencam api GET|POST|PUT|PATCH|DELETE PATH [--body JSON] [-o FILE]`

- [ ] **Step 1: 写失败测试**

```python
def test_api_get_cameras_matches_list(cli_env, capsys):
    run_cli(capsys, "cameras", "create", "--name", "x",
            "--source-type", "file", "--source-uri", "/tmp/x.mp4")
    listed = run_cli(capsys, "cameras", "list")
    via_api = run_cli(capsys, "api", "GET", "/cameras")
    assert via_api == listed


def test_api_write_binary_to_file(cli_env, capsys, tmp_path, monkeypatch):
    jpeg = b"\xff\xd8fake"
    real_request = cli._request

    def fake_request(client, method, path, **kwargs):
        if str(path).endswith("snapshot.jpg"):
            return jpeg
        return real_request(client, method, path, **kwargs)

    monkeypatch.setattr(cli, "_request", fake_request)
    dest = tmp_path / "x.jpg"
    out = run_cli(capsys, "api", "GET", "/cameras/1/snapshot.jpg", "-o", str(dest))
    assert out["ok"] is True
    assert dest.read_bytes() == jpeg
```

- [ ] **Step 2: 跑测试确认失败**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_cli.py::test_api_get_cameras_matches_list -v`  
Expected: FAIL（没有 `api` 子命令）

- [ ] **Step 3: 实现**

```python
def _api(args, client) -> None:
    method = args.method.upper()
    path = args.path if args.path.startswith("/") else f"/{args.path}"
    body = None
    if args.body:
        try:
            body = json.loads(args.body)
        except json.JSONDecodeError as exc:
            raise CliError(f"--body 不是合法 JSON：{exc}") from exc
    raw = bool(args.output)
    data = _request(client, method, path, body=body, raw=raw)
    if raw:
        _emit(_save_bytes(data, args.output), args.pretty)
    else:
        _emit(data, args.pretty)
```

`build_parser` 增加与 `cameras` 并列的 `api`：

```python
p = sub.add_parser("api", help="原始 REST 逃生舱")
p.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE"])
p.add_argument("path", help="如 /cameras 或 /events/1/clip")
p.add_argument("--body", help="JSON 对象字符串")
p.add_argument("-o", "--output", help="把响应体当文件保存（图片/视频）")
p.set_defaults(func=_api)
```

Skill「发现命令」加一句：`CLI 没有的接口用 opencam api METHOD PATH，不要 curl。` 不要把所有 REST 路径抄进 Skill。

- [ ] **Step 4: 跑测试确认通过**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_cli.py tests/test_skill_contract.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat: add opencam api escape hatch for unwrapped REST paths

EOF
)"
```
