# 快餐店方案包：按包创建多路摄像头

日期：2026-08-19  
状态：待实现  
范围：把快餐店方案包做成「包内多路摄像头」样板。应用时按包创建摄像头（演示片为初始源），用户停止后改成真实源。另外三个内置包保持旧格式。

## 背景

现有方案包是规则模板列表。`POST /api/packs/{id}/apply` 必须带 `camera_id`，把全部规则砸到同一路摄像头上。快餐店包里的门口越线、点餐排队、后厨闯入、闭店入侵因此会被画到同一画面。

系统里没有、也不在本期引入「门店」或「机位角色」。摄像头已经是聚合根（规则、事件、启停都挂在 `camera_id` 上）。方案包里也用摄像头：包声明多路，应用时把这些摄像头建出来。

现有 `PUT /cameras/{id}` 禁止改 `source_type` / `source_uri`（409「请新建摄像头」）。不放开停止后改源，「先按包创建、再换成真实 RTSP/文件」走不通。

## 目标

1. 快餐店包声明 4 路摄像头，每条规则只属于其中一路；每路带一段演示 mp4。
2. 应用快餐店时**不传** `camera_id`：复制演示片到视频库、创建 4 路 `stopped` 摄像头、规则挂到对应路上。
3. 餐饮 / 美发 / 零售保持旧格式：应用仍必须传 `camera_id`，行为与现在一致。
4. 停止后的摄像头可以改类型和视频源；运行中改源仍拒绝。
5. 市场页对快餐店展示 4 路清单，不再出现「应用到哪一台」下拉。
6. 测试覆盖双轨应用、改源、市场页静态检查；改 API 后重导 `docs/openapi.json`。

## 非目标

- 不建门店表、角色表、餐桌/出入口实体。
- 不改 `packs/restaurant/`、`packs/salon/`、`packs/retail-chain/` 的内容与目录结构。
- 不做 `GET /api/packs/{id}` 详情页、不做 `#/marketplace/{id}`、不在卡片上播预览视频。
- 不自动启动新建摄像头（避免一应用就 4 路 YOLO）。
- 不按条勾选规则、不静默去重、卸载包不级联删除已创建的摄像头/规则/副本视频。
- 不改源后自动按新分辨率重算规则坐标。
- 不做跨摄像头联合分析、Re-ID、店级指标。
- 演示片不要求实拍、不要求浏览器 `<video>` 能播（检测链路走 OpenCV 即可）。

## 包格式

### 双轨判定

- `pack.yaml` **没有** `cameras` 键 → 旧包。规则 yaml **禁止**出现 `camera` 字段。
- `pack.yaml` **有** `cameras` 键 → 新包。`cameras` 不能为空；每条规则 yaml **必须**有 `camera`，且值等于某路 `id`。

混用（旧包带 `camera`、新包规则缺 `camera`、`cameras: []`、规则指向不存在的 `id`、id 重复）→ 加载时 `PackError`，`list_packs` 跳过该包（与现有无效包行为一致）。

### 快餐店目录

```
packs/fast-food/
  pack.yaml
  cameras/
    door.mp4
    counter.mp4
    kitchen.mp4
    hall.mp4
  rules/
    door_flow.yaml          # camera: door
    queue_count.yaml        # camera: counter
    kitchen_intrusion.yaml  # camera: kitchen
    after_hours.yaml        # camera: hall
  prompts/review.txt
  README.md
```

`pack.yaml` 现有字段不变，新增：

```yaml
cameras:
  - id: door
    name: 门口
    source: cameras/door.mp4
  - id: counter
    name: 点餐
    source: cameras/counter.mp4
  - id: kitchen
    name: 后厨
    source: cameras/kitchen.mp4
  - id: hall
    name: 店内
    source: cameras/hall.mp4
```

约束：

- `id`：`^[a-z0-9][a-z0-9-]*$`，包内唯一。
- `name`：给人看的中文名，非空。
- `source`：相对包根的路径，必须落在包目录内（拒绝 `..`），文件必须存在。

规则 yaml 只比现在多一行 `camera: door`（等）。`name` / `type` / `cooldown` / `params` 及默认相对坐标、阈值、`active_hours` **不改**。

### 演示片

4 个 mp4 提交进仓库。由 `scripts/gen_fastfood_previews.py` 生成（OpenCV 写字 + 按该路默认相对坐标画线/框），生成结果入库；测试只断言文件存在且 OpenCV 能打开，不在测试里现画片子。

| `id` | 文件 | 画面 | 规则（相对坐标沿用现 yaml） |
|---|---|---|---|
| door | `cameras/door.mp4` | 16:9，中部横线 | 门口进出客流 `line: [[0.3,0.5],[0.7,0.5]]` 双向 |
| counter | `cameras/counter.mp4` | 中左矩形 | 点餐区排队超员 ≥5 |
| kitchen | `cameras/kitchen.mp4` | 右侧约 30% | 后厨闯入 |
| hall | `cameras/hall.mp4` | 全画面淡框 | 闭店入侵 `22:00-07:00` |

规格：640×360、静音、数秒循环即可；编码用 OpenCV `mp4v`。单文件控制在几百 KB。以后换成实拍只换文件，坐标不用动。

## 架构

```
pack.yaml cameras[] + rules/*.yaml（每条带 camera）
        │
        ▼ apply（新包）
  复制 mp4 → data_dir/uploads/  + 插入 videos 行
  创建 Camera（file，stopped，名称「{包名} · {路名}」）
  按该路演示片分辨率 scale_params → 写入 Rule.camera_id
        │
        ▼ 用户
  停止态 PUT 改源（RTSP 或另一文件）→ 规则页手调区域
```

旧包仍走现有 `apply_pack(pack_id, camera_id)`：探测那一台的分辨率，全部规则挂到该 id。

不新增 ORM 表。摄像头、规则、视频库仍是现有 `Camera` / `Rule` / `Video`。

## API

### `GET /api/packs`

`brief()` 增加 `cameras`：

- 新包：`[{ "id", "name", "source" }, ...]`；每条 `rules[]` 元素增加 `camera`（模板 id）。
- 旧包：`cameras` 为 `null`；`rules[]` 形状与现在相同（无 `camera`）。

市场页用 `cameras !== null` 分支 UI。不新增详情 GET。

### `POST /api/packs/{pack_id}/apply` → 201

请求体：

```json
{ "camera_id": 3 }   // 仅旧包；新包不要这个字段
```

`camera_id` 改为可选。响应**不再**是裸规则数组，统一为：

```json
{
  "cameras": [ /* CameraOut */ ],
  "rules": [ /* RuleOut */ ]
}
```

旧包：`cameras` 含被应用的那一台（已有行，不是新建）；`rules` 为本次新建的规则。

新包（快餐店）步骤：

1. 对每路：把 `source` 文件复制到 `settings.data_dir / "uploads"`。文件名用 basename；已存在则 `stem_1.ext`、`stem_2.ext`…（与视频上传同策略）。插入 `videos` 行（filename/path/size/探测到的 duration/width/height）。
2. 创建 `Camera`：`name` 见下、`source_type=file`、`source_uri=副本绝对路径`、`status=stopped`。禁止 `autostart`。
3. 用该路副本探测分辨率，只把 `camera` 等于该路 `id` 的模板 `scale_params` 后写成 `Rule`。
4. 返回新建的 4 台摄像头与全部新建规则。

摄像头命名：`{manifest.name} · {camera.name}`，例如 `快餐店 · 门口`。若该名称已存在，用 `快餐店 · 门口 (2)`、`(3)`… 直到不冲突。再应用一次必须新建一套，不得覆盖已有摄像头的源或规则。

错误（`detail` 逐字，均为 400，除非另注）：

| 情况 | HTTP | detail |
|---|---|---|
| 包不存在 | 400 | `方案包不存在: {id}` |
| 新包请求带了 `camera_id` | 400 | `该方案会创建摄像头，不要指定 camera_id` |
| 旧包未带 `camera_id` | 400 | `请指定要应用的摄像头` |
| 旧包摄像头不存在 | 400 | `摄像头不存在: {id}` |
| 演示片/视频源打不开 | 400 | `无法打开视频源: {path}` |

CLI：`opencam packs apply PACK_ID [CAMERA_ID]`。`camera_id` 改为可选位置参数。快餐店不跟 id；旧包必须跟。成功 stdout 为上述 JSON 对象（可 `json.loads`）。

### `PUT /cameras/{camera_id}`

仍用现有 `CameraUpdate`（`name` / `source_type` / `source_uri` 至少一个）。

- 只改 `name`：运行中、停止都允许（与现在一致）。
- 改 `source_type` 或 `source_uri`：
  - `status == running` → **409**，detail `请先停止摄像头再修改视频源`。
  - `status != running` → 写入并 200。空字符串 `source_uri` → 422。文件源指向不存在的路径：允许保存（与创建行为一致），启动时再失败。
- 改源**不**重算该摄像头上已有规则的像素坐标。

删除现有 409 文案 `类型和视频源创建后不可修改，请新建摄像头`。所有断言该文案的测试改为新文案 / 新行为。

## Web

### 方案市场 `marketplace.js`

- `cameras === null`：保持现在的下拉 +「应用」（旧包）。
- `cameras !== null`：列出每路 `name` 及属于该路的规则中文名；**没有** `select[data-cam-for]`；按钮「应用」，POST body 为 `{}`。
- toast 成功：说明创建了 N 路摄像头，请到「摄像头」页改成真实源后再启动。

### 摄像头页 `cameras.js`

去掉「已创建的摄像头只能改名称；更换类型或视频源请新建。」停止态行内可改类型与源地址，保存时 PUT `name` + `source_type` + `source_uri`。运行中保存仍只改名称（源控件只读或隐藏），避免误触 409。改源成功后 toast 提醒去「规则」页按真实画面调整区域。

## 测试

`OPENCAM_DETECTOR=mock uv run pytest` 全绿。至少覆盖：

1. 快餐店应用：4 路摄像头，名称分别为 `快餐店 · 门口` / `· 点餐` / `· 后厨` / `· 店内`；`status=stopped`；`source_type=file`；`source_uri` 指向 `data_dir/uploads/` 下且文件存在；规则的 `camera_id` 与归属一致（门口越线在门口，后厨闯入在后厨，以此类推）；`videos` 表有对应 4 行。
2. 再应用一次：第二套名称为 `快餐店 · 门口 (2)` 等；第一套的 `source_uri` 不变。
3. 旧包 `restaurant` 仍按 `camera_id` 应用；现有坐标换算断言保留。
4. 新包带 `camera_id` → 400 + `该方案会创建摄像头，不要指定 camera_id`。
5. 旧包不带 `camera_id` → 400 + `请指定要应用的摄像头`。
6. 停止后 PUT 改源 → 200；运行中 PUT 改源 → 409 + `请先停止摄像头再修改视频源`。替换 `tests/test_cameras_api.py` / `tests/test_cli.py` / `tests/test_web.py` 中「请新建摄像头」相关断言。
7. `GET /api/packs` 里 fast-food 的 `cameras` 四路 `name` 为 门口/点餐/后厨/店内。`marketplace.js` 静态检查：存在 `cameras` 非 null 分支；该分支里没有 `data-cam-for`（旧包分支仍可有）。
8. CLI：`packs apply fast-food` 退出 0，stdout 可 `json.loads`，含 `cameras` 与 `rules`。
9. 内置包列表仍包含四个 id；快餐店 `cameras` 非 null，另外三个为 null。
10. 改 API 后 `uv run python scripts/export_openapi.py`。

## 验收

- 市场页点快餐店「应用」，摄像头页出现 4 路演示源、均未运行；规则页能按摄像头滤到对应规则。
- 停止其中一路，改成自己的文件或 RTSP，保存成功；运行中改源被拒绝并出现指定中文。
- 餐饮包仍能选一台摄像头应用，不创建新摄像头。
- 上述测试全绿；OpenAPI 含新的 apply 请求/响应形状。
