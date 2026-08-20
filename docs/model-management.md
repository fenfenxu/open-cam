# 模型管理领域模型

## 当前边界

open-cam 当前是本地单机应用。模型资产没有租户归属，数据跟随本机 `data_dir` 保存；未来接入账号或云端市场时，再增加 `owner_id` / 租户隔离，不把本地模型误称为公共模型。

## 核心术语

- **模型资产（ModelAsset）**：用户可见、可搜索、可描述的逻辑模型对象。它回答“这是什么模型、从哪里来、能做什么”。
- **模型版本（ModelVersion）**：一次具体训练、上传或方案交付的产物，包含权重路径、sha256 哈希、框架、运行时、输入尺寸、指标和部署状态。版本不可原地改写，更新只能新增版本。
- **模型关联（ModelBinding）**：模型资产与规则、摄像头、分析方案、推理阶段或解决方案标识之间的关系。关联来源可以是手工或 AI 推荐；推荐关系必须保留置信度和理由，并先处于待审核状态。
- **来源类型（origin_type）**：系统内置 `builtin`、用户上传 `uploaded`、用户训练 `trained`（含二次训练），只表达产生方式。
- **交付方式（distribution_type）**：仅本机 `private`、用户发布 `published`、随解决方案交付 `solution`。一个训练模型可以后续被发布，也可以被打包进解决方案，因此不能把这些属性压成单一枚举。
- **模型类型**：目标检测、分类、分割、姿态、OCR、视觉大模型。来源、交付方式和模型能力是三条独立维度。
- **能力标签与输入输出契约**：资产上的 `capabilities` / `input_contract` / `output_contract` 描述模型能做什么、吃什么、产出什么（如 `person_detection` → `person.box`），供运行时解析与 AI 推荐匹配。

## 关系决策

模型不直接写入规则的 `params`，也不把本地权重路径放进规则。规则只声明所需能力，模型版本通过分析方案的推理阶段被解析进运行时计划。

主关联方向是：

```text
Camera → AnalysisProfile → PipelineStage → ModelVersion → Rule
```

规则级模型关联只作为兼容关系或局部覆盖，不作为长期主模型关系。

当前 API 支持 `rule` / `camera` / `analysis_profile` / `pipeline_stage` / `solution_pack` 关联。方案包可以通过 `analysis_profiles` 声明方案、阶段和能力契约；应用方案时会创建方案对象并绑定到摄像头。当前关联只记录管理关系，不自动改变运行中的 Pipeline；运行时模型解析属于下一阶段。

## 训练模型

通过训练模型版本接口登记产物时，如果没有传入已有模型资产，系统会自动创建一个 `trained` 模型资产，并把模型版本的 `model_asset_id` 指向它。这样“训练产物版本”和“模型资产展示对象”不会再次脱节。

## 上传与方案交付

- `POST /api/models/assets/upload` 接收权重文件，落盘到 `data_dir/models/uploads/`，登记 `uploaded` 资产并生成带 sha256 的首个版本。
- 方案包可以在 `pack.yaml` 里声明 `models:`（名称、类型、能力、可选包内权重文件）。安装时这些声明登记为 `builtin + solution` 资产并关联到 `solution_pack_id`；声明了权重文件时同时生成带哈希的模型版本。重复安装按包内模型 id 幂等，不覆盖用户编辑。

## 推荐关系

手工关联和 AI 推荐关联使用同一个模型关联实体：

- 手工关联：`relation_source=manual`；
- AI 推荐：`relation_source=ai_recommended`，必须保存 `confidence` 和 `reason`，初始 `relation_status=pending`；
- 通过 `/api/model-bindings/{id}/confirm` 或 `/reject` 审核推荐。AI 推荐不能覆盖已有手工关联，也不能因为推荐存在就直接上线模型。
- 通过 `POST /api/model-bindings/recommend` 按目标的能力标签、输入/输出契约、任务标识和名称描述生成候选；候选会保存 `model_version_id`、`confidence`、`reason` 与 `warnings`，且默认禁用。
- 推荐器是本地确定性匹配，不调用外部模型服务；重复推荐幂等，人工拒绝不会被再次推荐复活。

## 实施状态

模型资产正式化（计划 Stage 1）已完成：来源/交付拆分（`origin_type` + `distribution_type`）、能力标签与输入输出契约、版本哈希/框架/运行时均已落地；内置模型登记、训练登记、模型上传和方案安装都会生成可追溯资产；资产列表支持按来源、交付方式、模型类型、能力和描述搜索。原型的单一 `source_type` 列保留一个版本用于过渡（由新字段派生双写），下一版本删除。

Stage 2 已完成：`AnalysisProfile`、`PipelineStage`、`CameraBinding`、规则能力声明、方案包阶段声明，以及模型关联的待审核/确认/拒绝流程已落地。

Stage 3 已完成：摄像头启动时生成冻结的 `RuntimePlan`，按阶段能力、输入输出契约、设备、延迟预算、线上状态和产物哈希筛选模型；运行时模型不可用会让摄像头进入 `error` 并在 health 中返回用户可读原因。方案/阶段变更及模型部署、回滚会重启受影响的摄像头。事件保存分析方案版本、阶段、模型版本 id 和产物摘要，`GET /api/cameras/{id}/runtime-plan` 可查看当前计划。

Stage 4 已完成：推荐接口根据规则、摄像头、分析方案或推理阶段的需求生成待审核候选，保存可解释的置信度、理由、警告和具体版本；已有人工关系时不创建 AI 关系，确认/拒绝仍由人工操作，确认也不会自动把版本写入阶段或触发部署。
