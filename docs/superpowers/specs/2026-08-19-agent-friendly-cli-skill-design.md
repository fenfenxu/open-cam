# Agent 友好的 CLI 与 Skill 设计

日期：2026-08-19

## 问题

`opencam` CLI + `skills/opencam/SKILL.md` 是 Agent 的主入口，但当前（`origin/main`）有三处会系统性教坏 / 绊倒 Agent：

1. **stdout 不是单一契约。** `list`/`get` 吐 JSON，`delete` 吐「摄像头 1 已删除」，`snapshot` 吐「已保存 … 字节」。Agent 默认 `json.loads(stdout)`，删摄像头和抓帧会炸。
2. **Skill 是命令抄本，会过期。** 示例带 `--pretty`、`start 1 / stop 1 / delete 1` 不能粘贴、假多边形 `[[0,0],[320,0],…]` 会被原样创建。命令的源应是 `--help`，Skill 只写 `--help` 写不清的工作流。
3. **CLI 覆盖永远落后 API。** 没有逃生舱时，新接口（clip、未来 videos 之外的路径）Agent 只能猜 curl。

基线实测（新鲜 Agent，只给当时的 SKILL.md、禁止读仓库）：查告警会抄 `--pretty` 且不翻页；建规则会 snapshot 后仍抄 320×240；删摄像头会按 JSON 解析，但 CLI 给的是中文句子。

## 目标

Agent 把 `opencam` 当唯一入口时：成功输出永远能 `json.loads`；不知道命令就 `--help`；高频任务（查未确认告警、加区域规则）有可执行工作流，而不是过期示例。

## 契约

### CLI stdout

- 成功：stdout **只有**一行（或 `--pretty` 时多行）JSON。禁止中文散文。
- 删除 / 卸载：`{"ok": true, "id": <int 或 pack_id 字符串>}`
- 抓帧 / 事件快照：写文件后 stdout `{"ok": true, "path": "<绝对或给定路径>", "bytes": <int>}`
- 204 / 空 body：`{"ok": true}`
- 失败：信息在 **stderr**，退出码 1；stdout 不掺成功 JSON。
- 无子命令：`opencam` 打印 help，退出码 0（不要 argparse「required: resource」）。
- `--pretty` 仍可用，但是给人看的；Skill 示例默认不加。

### Skill

- `description` 以 `Use when` 开头，只写触发条件，不复述命令树。
- 正文是两条工作流（查未确认告警、加区域规则）+「发现命令」指向 `opencam --help` / `opencam <resource> --help`。
- fenced bash：**不要** `--pretty`、不要 ` / ` 速记、不要 `[[0,0]` 假多边形、不要 `opencam_client.py`、不要 curl。
- 明确：`ts` 是 Unix 秒；列表满 `--page-size`（默认 20）必须 `--offset` 再拉；多边形像素坐标必须来自当次 snapshot，量不到就问用户。

### 逃生舱

```text
opencam api METHOD PATH [--body JSON] [-o FILE]
```

薄封装 REST：CLI 没包到的接口走这里，不要教 Agent 手写 curl。二进制响应（clip / snapshot）用 `-o`。JSON 响应走同样的 `_emit`。

## 范围

做：stdout JSON 契约、无参数 help、Skill 重写、Skill 反模式 pytest、`opencam api`。

不做：改 REST 列表为 `{items,total}`（本轮靠 Skill 教翻页）、Go 重写 CLI、`--jq`、watch/WebSocket、训练 API 的专用子命令、Alembic/clip/doctor 等本工作区其它未提交改动。

## 验收

1. `opencam cameras delete ID` 的 stdout 可 `json.loads`，形如 `{"ok":true,"id":1}`。
2. `opencam cameras snapshot ID -o PATH` 同样 JSON，且文件落地。
3. `opencam` 无参数退出 0，stdout 含 `cameras` 与 `events`。
4. `tests/test_skill_contract.py` 锁住 Skill 反模式；`uv run pytest tests/test_cli.py tests/test_skill_contract.py` 全绿。
5. `opencam api GET /cameras` 返回与 `opencam cameras list` 相同形状的 JSON 数组。
6. Skill 字数保持精简（目标 < 400 词），不把 OpenAPI 抄进 SKILL.md。
