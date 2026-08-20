# 解决方案包模型与生命周期

本文定义解决方案包页面和后端 API 的共同模型。前端不得直接把 `pack.yaml` 当成完整领域模型；`pack.yaml` 只是发布包的交换格式。

## 一、核心边界

解决方案包有三个层次：

```text
SolutionPack（逻辑产品）
  └── SolutionPackVersion（不可变版本快照）
        ├── CameraTemplate × N（摄像头模板）
        ├── RuleTemplate × N（规则模板，可跨摄像头）
        ├── PromptTemplate × N（复核提示词）
        └── Asset × N（预览图、演示视频、图标等）

SolutionPackInstallation（本机安装记录）
  └── InstallationBinding（模板对象 → 本机摄像头/规则）
```

“安装”只把一个已发布版本放入本机可用目录并登记版本；“应用”才会创建或绑定摄像头和规则。这两个动作必须在 API 和界面上分开，避免用户误以为浏览或安装会启动摄像头。

## 二、领域模型

### 1. SolutionPack：逻辑产品

一个稳定的方案身份，可以有多个版本；每个版本可以包含 0~N 个摄像头模板和 0~N 条规则模板。`id` 永不因改名而改变。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 全局稳定 slug，`^[a-z0-9][a-z0-9-]*$` |
| `name` | string | 是 | 展示名称，如“快餐店” |
| `vertical` | string | 是 | 行业/场景，如“餐饮-快餐” |
| `description` | string | 否 | 面向用户的简介 |
| `author` | string | 否 | 作者或组织 |
| `icon_asset_id` | string/null | 否 | 图标资源 |
| `cover_asset_id` | string/null | 否 | 卡片封面 |
| `created_at` | datetime | 是 | 创建时间 |
| `updated_at` | datetime | 是 | 最近修改时间 |
| `current_version` | string/null | 否 | 当前推荐版本；没有发布版本时为空 |
| `visibility` | enum | 是 | `private` / `public` / `unlisted` |

### 2. SolutionPackVersion：版本快照

版本是编辑和发布的最小单位。发布后禁止原地修改；任何修改都必须从它复制出新的草稿版本。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | uuid/string | 是 | 版本记录 ID |
| `pack_id` | string | 是 | 所属 `SolutionPack.id` |
| `version` | semver string | 是 | 如 `1.2.0`，同一包内唯一 |
| `status` | enum | 是 | `draft` / `published` / `archived` |
| `min_opencam_version` | semver string | 是 | 最低兼容版本 |
| `manifest_schema_version` | integer | 是 | 包格式版本，不与产品版本混用 |
| `changelog` | string | 否 | 本版本变更说明 |
| `content_hash` | string/null | 发布后必有 | 规范化内容的 SHA-256 |
| `artifact_path` | string/null | 发布后必有 | 相对数据目录的包产物路径 |
| `created_by` | string/null | 否 | 创建者 |
| `published_at` | datetime/null | 发布时填写 | 发布后不可变 |
| `created_at` | datetime | 是 | 创建时间 |
| `updated_at` | datetime | 是 | 最近编辑时间 |

约束：一个 `pack_id` 同时只能有一个 `draft`；同一个 `pack_id + version` 不能重复；只有 `draft` 可以编辑；`archived` 只表示不再推荐，不影响已安装实例运行。

### 3. CameraTemplate：摄像头模板

描述方案需要哪些画面，不代表真实摄像头。一个方案版本可以声明多路摄像头，`key` 是版本内部引用，发布后不可修改。`SolutionPackVersion 1 : N CameraTemplate` 是明确的一对多关系。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | uuid/string | 是 | 数据记录 ID |
| `version_id` | string | 是 | 所属版本 |
| `key` | string | 是 | 规则引用键，如 `door` |
| `name` | string | 是 | 展示名称，如“门口” |
| `description` | string | 否 | 安装指引 |
| `source_type` | enum | 是 | `file` / `rtsp` / `any` |
| `sample_asset_id` | string/null | 否 | 预览/演示视频或图片 |
| `required` | boolean | 是 | 应用时是否必须绑定 |
| `sort_order` | integer | 是 | 展示顺序 |

同一个版本内 `key` 必须唯一；应用多摄像头方案时，每个模板都必须进入“创建新摄像头”或“绑定已有摄像头”二选一映射。不能因为某一路暂时没有真实源而静默跳过它，除非该模板 `required=false`。

实际 `source_uri`、真实摄像头名称、启停状态不放在模板中，应用时由用户填写或绑定。

### 4. RuleTemplate：规则模板

规则模板只保存可移植配置。区域和线使用 `0~1` 相对坐标，应用到真实画面时再按分辨率换算成像素。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | uuid/string | 是 | 数据记录 ID |
| `version_id` | string | 是 | 所属版本 |
| `key` | string | 是 | 版本内唯一键 |
| `camera_key` | string/null | 条件必填 | 指向同版本 `CameraTemplate.key`；旧式单摄像头包为空 |
| `name` | string | 是 | 规则展示名称 |
| `type` | enum | 是 | `zone_intrusion` / `loitering` / `object_count` / `zone_count` / `line_crossing` |
| `params` | object | 是 | 规则参数；坐标为相对坐标 |
| `cooldown` | number | 是 | 告警冷却秒数，`>= 0` |
| `enabled_by_default` | boolean | 是 | 应用后的默认启用状态 |
| `description` | string | 否 | 配置说明 |

`params` 必须由规则类型校验器校验，不能只用任意 JSON 放行。至少校验 polygon/line 点数、坐标范围、threshold、classes、active_hours 和 direction。

### 5. PromptTemplate：VLM 复核提示词

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `key` | string | 是 | 规则或场景引用键 |
| `content` | string | 是 | 模板正文，不保存 API key |
| `variables` | string[] | 是 | 允许替换的变量名 |
| `enabled` | boolean | 是 | 是否启用 |

### 6. Asset：资源

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | string | 是 | 资源 ID |
| `version_id` | string | 是 | 所属版本 |
| `kind` | enum | 是 | `icon` / `cover` / `sample_image` / `sample_video` / `attachment` |
| `relative_path` | string | 是 | 相对数据目录路径，禁止 `..` 穿越 |
| `mime_type` | string | 是 | 文件类型白名单校验 |
| `size_bytes` | integer | 是 | 文件大小 |
| `sha256` | string | 是 | 完整性校验 |
| `width` / `height` | integer/null | 否 | 图片/视频元数据 |
| `duration_sec` | number/null | 否 | 视频时长 |

### 7. SolutionPackInstallation：本机安装

安装记录指向精确版本，不指向“最新版”。一个安装实例对应一个方案版本，但这个版本可以包含多路摄像头模板；安装本身不要求所有模板立即绑定。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `id` | uuid/string | 是 | 安装实例 ID |
| `pack_id` | string | 是 | 方案逻辑 ID |
| `version_id` | string | 是 | 被安装的精确版本 |
| `source_type` | enum | 是 | `builtin` / `local_dir` / `zip` / `url` |
| `source_uri` | string/null | 否 | 来源地址；不保存敏感 token |
| `content_hash` | string | 是 | 安装时核对的哈希 |
| `install_path` | string | 是 | 相对数据目录路径 |
| `status` | enum | 是 | `installed` / `broken` / `uninstalled` |
| `installed_at` | datetime | 是 | 安装时间 |
| `last_checked_at` | datetime/null | 否 | 完整性检查时间 |

### 8. InstallationBinding：应用映射

安装和应用解耦后，需要记录模板对象映射到本机对象的关系，支持查看、升级和回滚。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `installation_id` | string | 是 | 安装实例 |
| `template_key` | string | 是 | 摄像头或规则模板 key |
| `target_type` | enum | 是 | `camera` / `rule` |
| `target_id` | integer | 是 | 本机 `Camera.id` 或 `Rule.id` |
| `mode` | enum | 是 | `created` / `bound` |
| `created_at` | datetime | 是 | 建立映射时间 |

应用操作必须记录这张映射表，卸载包时只允许清理 `mode=created` 且未被用户独立修改的对象；用户已有摄像头和规则不能被误删。

一个安装实例对应多条 binding，至少覆盖每个已应用的 `CameraTemplate`，以及该摄像头下创建的全部 `RuleTemplate`。

### 9. PackTopology：方案拓扑（派生视图）

拓扑不是额外的可编辑实体，而是由模板关系计算出的预览/应用模型：

```text
SolutionPackVersion
  ├── CameraTemplate: door
  │     ├── RuleTemplate: entrance_count
  │     └── RuleTemplate: loitering
  ├── CameraTemplate: cashier
  │     └── RuleTemplate: queue_count
  └── CameraTemplate: kitchen
        └── RuleTemplate: intrusion
```

规则通过 `camera_key` 指向一路摄像头；如果未来支持跨摄像头规则，则增加 `camera_keys: string[]`，与 `camera_key` 互斥，不能把多路关系塞进逗号分隔字符串。

## 三、状态机

```text
创建 → draft ──编辑/校验──┐
                         ├─发布→ published ──下架→ archived
                         └─删除→ deleted（仅草稿）

published ──预览→ preview session（临时、只读、可过期）
published ──安装→ installed
installed ──应用→ bindings + 本机 camera/rule
installed ──卸载→ uninstalled
```

- 预览不是发布，也不产生摄像头、规则或通知。
- 发布前必须完成 manifest、规则参数、资源路径、兼容版本和内容哈希校验。
- 已发布版本永远可被精确安装；“最新版本”只是列表排序，不是安装目标。
- 安装失败要清理 staging 目录，不能留下半个方案包；应用失败要回滚本次创建的数据库记录和复制的资源。

## 四、五个生命周期流程

### 1. 创建

创建接口只生成草稿：

```text
POST /api/solution-packs
POST /api/solution-packs/{pack_id}/versions
```

请求先写 `SolutionPack` 和 `SolutionPackVersion(status=draft)`，随后保存摄像头模板、规则模板、提示词和资源。草稿可以没有演示视频，但不能发布空规则包。

### 2. 编辑修改

编辑接口只允许修改草稿。界面保存使用整份草稿或明确的子资源接口，服务端每次保存都重新校验并返回 `updated_at`/`etag`，避免两个编辑窗口互相覆盖。

从已发布版本修改时执行“复制为新草稿”，不修改旧版本：

```text
POST /api/solution-packs/{pack_id}/versions/{version}/fork
```

### 3. 发布

发布接口执行完整校验、规范化 JSON/YAML、收集资源、生成 zip、计算 SHA-256，然后原子地把草稿状态改为 `published`：

```text
POST /api/solution-packs/{pack_id}/versions/{version}/publish
```

发布成功后生成 changelog、artifact_path、content_hash 和 `published_at`。同一逻辑包只能有一个推荐版本，但历史已发布版本仍可安装。

### 4. 预览

预览默认只读，支持草稿和已发布版本：

```text
GET /api/solution-packs/{pack_id}/versions/{version}/preview
```

返回内容包括基本信息、摄像头模板列表、每路摄像头下的规则模板、规则作用的示意图/演示视频、兼容性检查结果和应用预估结果。预览支持按摄像头切换画面，也支持总览方案拓扑。预览可以读取 sample asset、绘制相对坐标区域、展示规则参数，但不得写入 Camera/Rule/Event，也不得启动通知或 VLM。

### 5. 安装与应用

安装精确的已发布版本。一次安装可以携带整个多摄像头方案，但不强制一次性应用全部摄像头：

```text
POST /api/solution-pack-installations
{
  "pack_id": "fast-food",
  "version": "1.0.0",
  "source": "local_dir | zip | url"
}
```

安装流程为：下载/读取 → staging → 解包安全检查 → manifest/schema/哈希校验 → 资源检查 → 原子移动 → 写安装记录。安装后用户再选择“创建演示摄像头”或“绑定已有摄像头”，调用应用接口：

```text
POST /api/solution-pack-installations/{installation_id}/apply
```

应用请求必须明确每个 `CameraTemplate.key` 的来源和目标，例如 `door` 绑定门口真实摄像头、`cashier` 创建演示摄像头。创建模式复制 sample video 到 uploads，绑定模式只引用已有 Camera。所有本次新建的 Camera、Video、Rule 和 Binding 在一个数据库事务中提交；多路中任何一路失败，整次应用回滚，不能留下半套方案。

## 五、与当前文件格式的兼容映射

当前 `pack.yaml` 可以作为 `SolutionPack` + `SolutionPackVersion` 的导入格式：

- `id/name/vertical/description/author` → `SolutionPack`；
- `version/min_opencam_version` → `SolutionPackVersion`；
- `cameras[]` → `CameraTemplate`；其中 `source` 是 `sample_asset`，不是实际摄像头源；
- `models[]` → 随包交付的模型声明；安装时登记为 `builtin + solution` 的 `ModelAsset`（含能力标签），声明了包内权重文件时同时生成带 sha256 的 `ModelVersion`；
- `rules/*.yaml` → `RuleTemplate`；`camera` → `camera_key`；
- `prompts/*.txt` → `PromptTemplate`；
- `README.md`、视频和其他文件 → `Asset`；
- 目录安装完成后创建 `SolutionPackInstallation`，`apply_pack()` 改为基于安装版本创建 `InstallationBinding`。

兼容旧包时可以继续读现有目录，但新建、编辑和发布必须生成带 `manifest_schema_version` 的版本快照。前端展示字段以后端领域模型为准，不直接依赖目录结构或文件名。

## 六、前端页面的最小页面边界

模型确定后，前端至少拆成四个页面/面板：

1. **方案列表**：逻辑包、当前发布版本、草稿状态、安装状态。
2. **方案编辑器**：只编辑草稿；分基本信息、摄像头模板、规则模板、提示词、资源五个区块。
3. **版本预览**：显示校验结果和画面叠加，只有“发布”和“返回编辑”。
4. **安装/应用向导**：先选精确版本，再以摄像头列表逐路绑定或创建，显示每路规则数量、缺失必需源和最终将创建的对象，最后提供回滚提示。

这样可以保证页面不会把“编辑当前安装目录”“发布一个版本”“把模板应用到摄像头”混成一个危险操作。
