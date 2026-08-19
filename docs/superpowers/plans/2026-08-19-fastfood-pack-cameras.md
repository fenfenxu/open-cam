# 快餐店方案包：按包创建多路摄像头 Implementation Plan

> **For agentic workers:** 验收只对照本计划与规格 `docs/superpowers/specs/2026-08-19-fastfood-pack-cameras-design.md`。REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans。从 `origin/main` 开分支。PR 标题和正文必须含对应 Multica issue key（如 `CAM-xx` / `Closes CAM-xx`）。

**Goal:** 快餐店方案包按 4 路摄像头创建演示源；旧包仍选一台应用；停止后可改源。

**Architecture:** `pack.yaml` 可选 `cameras[]`。有则 apply 复制演示 mp4 到 `data_dir/uploads/`、插入 `videos`、新建 `stopped` 的 `Camera`、规则按 `camera` 字段挂到对应路。无则保持现有 `camera_id` 整包打入。不新增 ORM 表。

**Tech Stack:** 现有 FastAPI + SQLAlchemy + 无构建 JS + pytest（`tmp_settings` / mock detector）+ OpenCV 生成占位 mp4。

## Global Constraints

- 规格原文：`docs/superpowers/specs/2026-08-19-fastfood-pack-cameras-design.md`（错误文案、HTTP 码以规格为准，逐字使用）。
- 基线：`origin/main`。不要把工作区未提交的 Alembic / notify / doctor / Makefile 改动卷进本 PR。
- Python ≥ 3.12；`from __future__ import annotations`；用户可见文案中文，标识符英文。
- **禁止**门店/角色/餐桌表、改另外三个内置包内容、`GET /api/packs/{id}` 详情页、自动启动新建摄像头、改源后重算规则坐标、卸载时级联删摄像头。
- 测试用 `tmp_settings`，`OPENCAM_DETECTOR=mock`，不下载模型、不依赖网络。
- 改 API 后必须 `uv run python scripts/export_openapi.py`。
- 命令：`OPENCAM_DETECTOR=mock uv run pytest`。
- 演示片画面用 OpenCV `putText` 写英文 id（`door` 等），避免 CI 缺中文字体；中文名只出现在 yaml / 摄像头 `name`。

## 范围边界

做：快餐店新格式 + 演示片、双轨 apply、停止后改源、市场页/摄像头页/CLI/OpenAPI/README。  
不做：另外三包拆摄像头、详情页、门店抽象、自动启动。

## 验收标准（DoD）

对照规格「验收」+「测试」1–10。HTTP `detail` 逐字：

- `该方案会创建摄像头，不要指定 camera_id`
- `请指定要应用的摄像头`
- `请先停止摄像头再修改视频源`
- `方案包不存在: {id}`
- `摄像头不存在: {id}`
- `无法打开视频源: {path}`

## 拆解思路（子 Issue / stage）

| Stage | 子任务 | 可独立验收 |
|---|---|---|
| 1 | 包格式 + 演示片 + apply 双轨（含 API 响应形状） | `tests/test_packs.py` 快餐店 4 路创建；旧包 restaurant 仍按 camera_id |
| 2 | 停止后改源（API + 摄像头页 + CLI） | 停止 200 / 运行中 409；`cameras.js` 停止态可改源 |
| 3 | 市场页 + OpenAPI + README | `marketplace.js` 新包无下拉；openapi 含 apply 对象响应 |

文件总览：

- Modify: `opencam/packs/manifest.py`、`installer.py`、`apply.py`、`api/packs.py`、`api/cameras.py`、`models.py`（CameraUpdate 注释）、`cli.py`、`web/pages/marketplace.js`、`web/pages/cameras.js`、`packs/fast-food/**`、`tests/test_packs.py`、`tests/test_cameras_api.py`、`tests/test_cli.py`、`tests/test_web.py`、`tests/test_rule_name.py`（仅 restaurant 调用约定）、`docs/openapi.json`、`README.md`
- Create: `scripts/gen_fastfood_previews.py`、`packs/fast-food/cameras/{door,counter,kitchen,hall}.mp4`

---

### Task 1: 包格式、演示片、apply 双轨

**Files:**
- Create: `scripts/gen_fastfood_previews.py`、`packs/fast-food/cameras/*.mp4`
- Modify: `opencam/packs/manifest.py`、`installer.py`、`apply.py`、`api/packs.py`、`cli.py`（仅 `packs apply`）、`packs/fast-food/pack.yaml`、`packs/fast-food/rules/*.yaml`、`packs/fast-food/README.md`、`tests/test_packs.py`、`tests/test_rule_name.py`
- Test: `OPENCAM_DETECTOR=mock uv run pytest tests/test_packs.py tests/test_rule_name.py tests/test_cli.py -v`

**Interfaces:**
- Consumes: 现有 `Rule` / `Camera` / `Video`、`scale_params`、`probe_resolution`、`get_pack`。
- Produces:
  - `PackManifest.cameras: list[PackCamera] | None`
  - `RuleTemplate.camera: str | None`
  - `ApplyResult(cameras: list[Camera], rules: list[Rule])`
  - `apply_pack(pack_id: str, session: Session, camera_id: int | None = None) -> ApplyResult`
  - `POST /api/packs/{id}/apply` body `{camera_id?: int}` → 201 `{cameras: CameraOut[], rules: RuleOut[]}`
  - `GET /api/packs` brief 增加 `cameras`（新包为列表，旧包 `null`）；新包每条 rule 含 `camera`

- [ ] **Step 1: 生成演示片并改快餐店 yaml**

`scripts/gen_fastfood_previews.py`：640×360、mp4v、5fps、15 帧。每路画对应相对坐标（与 yaml 相同）+ `putText` 英文 id。写出 `packs/fast-food/cameras/{door,counter,kitchen,hall}.mp4`。运行一次并把 mp4 提交进仓库。

`pack.yaml` 增加规格中的 `cameras` 列表。四条规则 yaml 各加一行：`camera: door` / `counter` / `kitchen` / `hall`。README 改成：应用将创建 4 路摄像头，演示源可先跑，再到摄像头页改真实源，规则页按实际画面调区域。

- [ ] **Step 2: 写失败测试（先改 `tests/test_packs.py` 里 fast-food 用例，确认红）**

把 `test_apply_fast_food_pack` 改成不再传入已有 `camera_id`。`apply_pack` 新签名：`apply_pack(pack_id, session, camera_id=None)`。`test_apply_builtin_pack` / `test_apply_unknown_pack` / `test_rule_name.py` 的 restaurant 调用改为 `apply_pack(..., session, camera_id=camera.id)`，返回值改读 `.rules`。

追加（可写在 `test_packs.py` 末尾）：

```python
from opencam.models import Video


def test_list_brief_cameras_dual_track(tmp_settings):
    packs = {p["id"]: p for p in installer.list_packs()}
    assert packs["fast-food"]["cameras"] is not None
    names = {c["name"] for c in packs["fast-food"]["cameras"]}
    assert names == {"门口", "点餐", "后厨", "店内"}
    assert {r.get("camera") for r in packs["fast-food"]["rules"]} == {
        "door", "counter", "kitchen", "hall"}
    assert packs["restaurant"]["cameras"] is None
    assert all("camera" not in r for r in packs["restaurant"]["rules"])


def test_apply_fast_food_creates_four_cameras(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        result = apply_pack("fast-food", session)
        names = sorted(c.name for c in result.cameras)
        assert names == ["快餐店 · 后厨", "快餐店 · 店内", "快餐店 · 点餐", "快餐店 · 门口"]
        assert all(c.status == "stopped" and c.source_type == "file"
                   for c in result.cameras)
        uploads = tmp_settings.data_dir / "uploads"
        for cam in result.cameras:
            path = Path(cam.source_uri)
            assert path.exists()
            assert uploads.resolve() in path.resolve().parents
        by_name = {c.name: c.id for c in result.cameras}
        rules = {r.name: r for r in result.rules}
        assert rules["门口进出客流"].camera_id == by_name["快餐店 · 门口"]
        assert rules["点餐区排队超员"].camera_id == by_name["快餐店 · 点餐"]
        assert rules["后厨闯入"].camera_id == by_name["快餐店 · 后厨"]
        assert rules["闭店后入侵"].camera_id == by_name["快餐店 · 店内"]
        assert rules["闭店后入侵"].params["active_hours"] == "22:00-07:00"
        assert session.query(Video).count() == 4
    finally:
        session.close()


def test_apply_fast_food_second_set_gets_suffix(tmp_settings):
    init_db(tmp_settings.db_url)
    session = get_session()
    try:
        first = apply_pack("fast-food", session)
        first_uri = next(c.source_uri for c in first.cameras
                         if c.name == "快餐店 · 门口")
        second = apply_pack("fast-food", session)
        names = {c.name for c in second.cameras}
        assert "快餐店 · 门口 (2)" in names
        session.refresh(first.cameras[0])
        still = session.query(Camera).filter_by(name="快餐店 · 门口").one()
        assert still.source_uri == first_uri
    finally:
        session.close()


def test_preview_files_openable(tmp_settings):
    from opencam.packs.installer import builtin_packs_dir
    import cv2
    for name in ("door", "counter", "kitchen", "hall"):
        path = builtin_packs_dir() / "fast-food" / "cameras" / f"{name}.mp4"
        assert path.is_file(), path
        cap = cv2.VideoCapture(str(path))
        assert cap.isOpened()
        cap.release()
```

HTTP 层测可放同文件或 `tests/test_packs.py` 用 TestClient：

```python
def test_apply_fast_food_rejects_camera_id(tmp_settings):
    from fastapi.testclient import TestClient
    from opencam.main import app
    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        resp = client.post("/api/packs/fast-food/apply", json={"camera_id": 1})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "该方案会创建摄像头，不要指定 camera_id"


def test_apply_legacy_requires_camera_id(tmp_settings, tmp_path):
    from fastapi.testclient import TestClient
    from opencam.main import app
    init_db(tmp_settings.db_url)
    with TestClient(app) as client:
        resp = client.post("/api/packs/restaurant/apply", json={})
        assert resp.status_code == 400
        assert resp.json()["detail"] == "请指定要应用的摄像头"
        video = tmp_path / "cam.mp4"
        _make_video(video)
        cam = client.post("/cameras", json={
            "name": "t", "source_type": "file", "source_uri": str(video),
        }).json()
        resp = client.post("/api/packs/restaurant/apply",
                           json={"camera_id": cam["id"]})
        assert resp.status_code == 201
        body = resp.json()
        assert "cameras" in body and "rules" in body
        assert body["cameras"][0]["id"] == cam["id"]
        assert len(body["rules"]) == 3
```

- [ ] **Step 3: 实现 manifest 双轨**

`PackCamera`：`id`（pattern 与 pack id 相同）、`name`（min_length 1）、`source`（非空字符串）。

`PackManifest.cameras: list[PackCamera] | None = None`。若提供则至少 1 条、id 唯一。

`RuleTemplate.camera: str | None = None`。

`Pack.__init__` 在 load 之后：

- 解析每个 `source`：`(pack_dir / source).resolve()`，必须 `is_file()` 且路径前缀为 `pack_dir.resolve()`（拒绝 `..`）。否则 `PackError`。
- 有 `cameras`：每条规则 `camera` 必填且 ∈ ids。
- 无 `cameras`：每条规则 `camera` 必须为 None。

`brief()`：有 cameras 时返回 `[{"id","name","source"}, ...]`，rules 带 `camera`；否则 `cameras: None`，rules 不含 `camera` 键。

- [ ] **Step 4: 实现 apply_pack**

```python
@dataclass
class ApplyResult:
    cameras: list[Camera]
    rules: list[Rule]
```

新包（`manifest.cameras` 非空）且 `camera_id is not None` → `PackError("该方案会创建摄像头，不要指定 camera_id")`。  
旧包且 `camera_id is None` → `PackError("请指定要应用的摄像头")`。  
旧包：现有逻辑，返回 `ApplyResult(cameras=[那一台], rules=created)`。  
未知包：保持 `方案包不存在: {id}`。

新包：对每路

1. `shutil.copy2` 演示片到 `settings.data_dir / "uploads"`，basename 冲突则 `stem_1.ext`…（与 `videos._safe_dest` 同策略，但不要调用会抛 HTTPException 的上传函数）。
2. `probe_resolution` 副本；失败则 `PackError(f"无法打开视频源: {path}")`。
3. 插入 `Video`（filename/path/size_bytes/duration_sec/width/height/created_at）。duration 可用 `CAP_PROP_FRAME_COUNT / FPS`，探测失败则 None。
4. `Camera(name=unique_name, source_type="file", source_uri=str(副本), status=CAMERA_STOPPED)`。`unique_name`：`{pack.name} · {cam.name}`，冲突则 ` (2)`、`(3)`…（查 `Camera.name`）。
5. 将该路 `tpl.camera == cam.id` 的模板 `scale_params` 后写成 `Rule`。

一次 commit。不要 autostart。

- [ ] **Step 5: API + CLI apply**

`ApplyRequest.camera_id: int | None = None`。  
响应模型例如 `PackApplyOut(cameras: list[CameraOut], rules: list[RuleOut])`，`status_code=201`。用已有 `camera_out` 填 health。  
`cli.py`：`packs apply` 的 `camera_id` 改为 `nargs="?"` `type=int`。body：有 camera_id 才放进 JSON，否则 `{}`。help 改为「新包不跟摄像头 id，旧包必须跟」。

- [ ] **Step 6: 跑测试至绿并提交**

`OPENCAM_DETECTOR=mock uv run pytest tests/test_packs.py tests/test_rule_name.py -v`  
再跑一遍 CLI 里若已有 packs apply 测试则更新。提交本 stage 文件（含 mp4）。

---

### Task 2: 停止后改源

**Files:**
- Modify: `opencam/api/cameras.py`、`opencam/models.py`（CameraUpdate docstring）、`opencam/cli.py`（update help）、`opencam/web/pages/cameras.js`、`tests/test_cameras_api.py`、`tests/test_cli.py`、`tests/test_web.py`
- Test: `OPENCAM_DETECTOR=mock uv run pytest tests/test_cameras_api.py tests/test_cli.py tests/test_web.py -v`

**Interfaces:**
- Consumes: `CameraUpdate`、`CAMERA_RUNNING`。
- Produces: 停止后 PUT 改源 200；运行中改源 409 `请先停止摄像头再修改视频源`。删除文案 `类型和视频源创建后不可修改，请新建摄像头`。

- [ ] **Step 1: 改红测试**

`test_put_source_is_immutable` → 停止态改源 200，`source_uri` 变为 `/tmp/other.mp4`。  
`test_put_source_type_is_immutable` → 停止态改 `source_type=rtsp` 且带一个 `source_uri`（或只改 type）200。  
`test_put_source_while_running_still_immutable` 与 `test_put_source_type_while_running_conflict`：仍 409，detail **精确等于** `请先停止摄像头再修改视频源`。  
`test_cli.py` `test_cameras_update_rejects_source` 改为停止态 update `--source-uri /tmp/y.mp4` 成功，get 为新 uri。另加：先 start 再 update 源 → 退出码 1，stderr 含 `请先停止摄像头再修改视频源`（若 CLI start 在测试环境可跑文件源；不可跑则只测 API）。  
`test_web.py` `test_cameras_page_has_video_library`：删除「请新建」「class=c-type 不存在」「source_uri: row.querySelector 不存在」。改为断言存在停止态改源（`c-type` / `c-uri` 或等价），且 JS 含 `请先停止` 或「规则」页提醒文案。

空 `source_uri`：`{"source_uri": ""}` → 422。

- [ ] **Step 2: 实现 PUT**

`update_camera`：若 `source_type` 或 `source_uri` 在 `model_fields_set`：

- `camera.status == CAMERA_RUNNING` → 409 `请先停止摄像头再修改视频源`
- 否则写入；`source_uri` 若是 `""` → 422（Pydantic `min_length=1` 或手写）。

只改 name 不碰上面的分支。不要重算规则。

CLI help 去掉「已废弃」「不可改」。update 仍把提供的字段放进 PUT body。

- [ ] **Step 3: 摄像头页**

去掉「只能改名称…请新建」。每行：名称始终可编辑。`status !== 'running'` 时类型 `<select>`、源 `<input>` 可编辑，保存 PUT `name`+`source_type`+`source_uri`；运行中只 PUT `name`，源只读。保存成功若改了源，toast 提到「规则」页按真实画面调整区域。

- [ ] **Step 4: 测试全绿并提交**

---

### Task 3: 市场页、OpenAPI、README

**Files:**
- Modify: `opencam/web/pages/marketplace.js`、`docs/openapi.json`（经脚本）、`README.md`（方案包那两段）
- Test: `OPENCAM_DETECTOR=mock uv run pytest tests/test_web.py tests/test_cli.py -v`；`uv run python scripts/export_openapi.py`

**Interfaces:**
- Consumes: `GET /api/packs` 的 `cameras` 字段；Task 1 的 apply JSON。
- Produces: 新包卡片无 `select[data-cam-for]`；应用 POST `{}`；toast 含创建路数与去摄像头页改源。

- [ ] **Step 1: 市场页测试（静态）**

在 `test_web.py` 增加：

```python
def test_marketplace_new_pack_has_no_camera_select(client):
    js = client.get("/static/pages/marketplace.js").text
    assert "cameras" in js
    assert "null" in js
    # 新包分支不得绑定 data-cam-for（旧包分支仍可有）
    assert "请到「摄像头」页" in js or "改成你的真实源" in js or "真实源" in js
```

实现：`p.cameras == null`（注意 JSON null）走旧 UI；否则列出每路 `c.name` 和 `p.rules.filter(r => r.camera === c.id)` 的规则名，按钮 `data-apply`，**不要** `data-cam-for`。click 时若该卡没有 cam select，body `{}`。toast：`已创建 ${data.cameras.length} 路摄像头，请到「摄像头」页改成真实源后再启动`。

旧包仍读 `select[data-cam-for]`，body `{camera_id}`。

- [ ] **Step 2: CLI 测 packs apply fast-food**

`tests/test_cli.py` 增加：`run_cli(..., "packs", "apply", "fast-food")` stdout 有 `cameras`/`rules`，长度为 4。旧包 `packs apply restaurant <id>` 仍能用。

- [ ] **Step 3: OpenAPI + README**

`uv run python scripts/export_openapi.py`。确认 apply 请求 `camera_id` 非必填、响应是对象不是 array。

README：快餐店应用会创建门口/点餐/后厨/店内 4 路演示摄像头；另外三包仍选一台应用。CLI 示例改为 `opencam packs apply fast-food` 与 `opencam packs apply restaurant 1`。

- [ ] **Step 4: 全量 `OPENCAM_DETECTOR=mock uv run pytest` 全绿并提交**

---

## Self-review

- 规格测试 1–10 均有对应断言（Task1：1,2,3,4,5,8,9 + 演示片；Task2：6；Task3：7,10）。
- 无 TBD；`apply_pack` 新签名在 Task1 写死，restaurant 调用全部改为 keyword `camera_id=`。
- 错误文案与规格表逐字一致。
- 不改 salon/retail/restaurant 包文件。
