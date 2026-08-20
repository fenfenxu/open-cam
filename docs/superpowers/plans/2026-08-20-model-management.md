# 模型管理与运行时模型选择计划

## 目标

为 open-cam 建立可解释、可追溯、可扩展的模型管理体系：用户能管理模型资产和版本，系统能根据分析方案选择兼容模型，规则不再直接依赖固定权重路径；同时保留本地单机产品的简单边界，为未来模型市场和租户隔离预留演进空间。

## 已有原型（本计划的起点）

当前工作区已经有一版未经最终领域确认的原型：

- `model_assets`、`model_bindings` 表和 0008 迁移；
- 模型资产名称、描述、单一来源类型、模型能力类型；
- `/api/models/assets` CRUD 和模型关联 API；
- 系统默认 YOLO 模型自动登记；
- 训练模型版本自动关联 `trained` 模型资产；
- Web「模型管理」页面；
- OpenAPI、后端测试和领域说明。

原型中的 `source_type` 五值枚举不作为最终设计：因为“自己训练”与“用户发布/解决方案交付”可以同时成立，正式实现需要拆成来源和交付两个维度。

## 领域模型

### ModelAsset：逻辑模型资产

回答“这是什么模型”。包含：名称、描述、来源、交付方式、模型类型、能力标签、输入输出契约、适用场景、限制条件和当前状态。

来源只表达产生方式：

- `builtin`：系统内置；
- `uploaded`：用户上传；
- `trained`：用户训练或二次训练。

交付方式独立表达传播方式：

- `private`：仅本机使用；
- `published`：用户发布；
- `solution`：随解决方案交付。

一个模型可以是 `trained + published`，也可以是 `trained + solution`。

### ModelVersion：不可变模型版本

回答“具体使用哪一份产物”。包含：权重文件、哈希、框架、运行时、输入尺寸、训练任务、评估指标、版本状态、部署和回滚关系。

模型资产可以有多个版本；模型版本不能被原地改写，更新只能新增版本。

### AnalysisProfile：分析方案

回答“这个场景应该怎么检测”。包含场景、输入要求、处理帧率、延迟约束、推理阶段和规则集合。

### PipelineStage：推理阶段

每个阶段声明能力和输出契约，例如：

- `person_detection` → `person.box`；
- `tracking` → `person.track`；
- `uniform_classification` → `uniform.attribute`；
- `plate_ocr` → `plate.text`。

阶段可以引用一个固定模型版本，也可以引用一个模型槽位，由运行时解析当前可用的 live 版本。

### Rule：业务规则

规则只声明需要的能力，例如 `person.track`、`fire.box`、`helmet.attribute`，不保存模型路径。

### CameraBinding：摄像头绑定

摄像头绑定一个明确的 `AnalysisProfile` 版本。启动摄像头时生成不可变的 `RuntimePlan`，记录本次实际使用的 profile 和 model version。

### ModelBinding：模型关联

用于保存模型资产/版本与分析阶段、摄像头、规则或方案键之间的关系。关系来源包括：

- `manual`：人工确认；
- `ai_recommended`：AI 推荐，必须保存置信度和理由；
- `rejected`：人工拒绝推荐。

分析方案阶段是主绑定目标；规则和摄像头绑定属于过渡兼容和局部覆盖。

## 运行时流程

```text
Camera
  → resolve CameraBinding
  → load AnalysisProfile version
  → resolve PipelineStage capabilities
  → select compatible live ModelVersion
  → validate input/output/device constraints
  → build RuntimePlan
  → run capture → inference → tracking → rules → events
```

模型无法满足能力、输入或设备约束时必须明确报错或禁用对应场景，不能静默回退成无关模型。

事件需要记录：

- `analysis_profile_version`；
- `pipeline_stage`；
- `model_version_id`；
- `artifact_digest`。

## AI 推荐策略

推荐器读取模型名称、描述、能力标签、类别、输入输出契约、模型类型、方案和规则需求，返回候选关系：

```json
{
  "model_version_id": 12,
  "target_stage": "person_detection",
  "confidence": 0.91,
  "reason": "模型描述和输出类别均匹配人员检测能力",
  "warnings": []
}
```

推荐不能自动上线；手工关系优先于 AI 推荐，AI 推荐优先于系统默认值。用户可以修改名称、描述、能力标签和适用场景，以修正二次训练模型的语义信息。

## 本地单机与租户

当前阶段不引入租户字段，所有模型资产属于本机工作区并保存在 `data_dir`。未来接入云端市场时增加 `owner_type`、`owner_id`、`visibility` 和发布权限；本地模型默认不上传、不公开。

## 开发阶段与验收标准

### Stage 1：模型资产正式化

- 将原型单一 `source_type` 拆为 `origin_type` + `distribution_type`；
- 补齐模型能力、输入输出契约、框架、运行时和文件哈希；
- 支持内置、上传、训练/二次训练模型的登记、编辑、归档和版本关联；
- 训练登记、模型上传和方案安装均能生成可追溯资产；
- 资产列表能按来源、交付方式、模型类型、能力和描述搜索。

### Stage 2：分析方案与绑定

- 新增 `AnalysisProfile`、`PipelineStage`、`CameraBinding`；
- 方案包可以声明分析阶段和能力要求；
- 规则改为声明能力，不直接指定权重路径；
- 人工绑定和 AI 推荐绑定可查看、确认、拒绝；
- 同一模型可以被多个规则和多个方案复用。

### Stage 3：运行时解析

- 摄像头启动时生成 `RuntimePlan`；
- 只加载与阶段能力、输入、设备和延迟约束兼容的模型；
- 模型变更需要重新加载或重启对应摄像头；
- 事件记录实际使用的模型版本和哈希；
- 模型不可用时给出明确健康状态和用户可读原因。

### Stage 4：AI 推荐

- 基于描述和能力契约生成候选关联；
- 提供推荐理由、置信度、警告和人工确认；
- 不允许 AI 推荐覆盖人工关系或自动上线；
- 为二次训练模型提供名称、描述、能力标签的人工修正入口。

### Stage 5：租户与模型市场

- 仅在云端发布、共享或交易需求确定后实施；
- 增加所有权、可见性、发布、审核、下载和版本同步；
- 不影响当前本地单机模型数据。

## 本轮非目标

- 不在本轮实现自动模型选择；
- 不在本轮实现 AI 推荐服务；
- 不在本轮引入租户和云端模型市场；
- 不把分类模型直接当作目标检测模型运行；
- 不通过修改规则 JSON 的方式绑定模型。

## 总体验收

1. 用户可以看到并维护模型名称、描述、来源、交付方式和模型类型。
2. 一个模型可以有多个版本，版本可部署、回滚并追踪产物。
3. 一个模型可以被多个方案阶段复用，规则不保存权重路径。
4. 手工关联和 AI 推荐关联可区分、可解释、可确认和可拒绝。
5. 摄像头运行时能明确知道实际使用的分析方案和模型版本。
6. 本地单机数据升级不丢失；未来租户字段不会破坏现有本地数据。
