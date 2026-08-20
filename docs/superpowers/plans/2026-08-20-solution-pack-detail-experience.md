# 解决方案包详情与体验 Implementation Plan

日期：2026-08-20

状态：已确认

**Goal:** 把方案市场从“卡片上直接应用规则包”升级为完整闭环：用户可进入独立详情页，理解业务价值和多机位部署，观看确定性的检测效果，在本机隔离试跑单个场景，预览应用变更，确认后创建可追踪的方案部署，并按清单完成校准与启用。

**产品依据:** `docs/superpowers/specs/2026-08-20-solution-pack-detail-experience-design.md`

**验收唯一决策点:** 本计划的“验收标准”和各任务 DoD。产品方案用于解释设计动机；若实现细节冲突，以本计划为准，变更验收标准必须先更新并评审本计划。

## 1. 现状与关键差距

- `GET /api/packs` 只有列表 brief，没有详情读取和安全媒体访问。
- Web 只有 `/marketplace` 卡片网格；卡片暴露新旧包判断并直接应用。
- 快餐包已有门口、点餐、后厨、店内四路 mp4，但只是 `mp4v` 灰底线框占位片，没有可展示的目标、触发过程和事件结果，也不保证浏览器播放。
- `apply_pack()` 直接创建 Camera/Rule/Video，没有应用前计划、内容指纹、文件失败补偿和资源归属。
- 旧格式包应用到运行中摄像头时，规则立即启用，未校准就可能进入正式判断。
- 系统不能回答某组摄像头/规则来自哪个包和版本，也不能跨会话继续“换源、校准、启用”的部署流程。

## 2. 产品与技术决策

1. 详情使用 `/marketplace/{pack_id}`；复用当前客户端 catch-all 与 FastAPI SPA fallback，不要求构建时枚举第三方包 id。
2. “效果演示”和“本机试跑”严格区分：
   - 效果演示播放包内浏览器兼容的原始/结果媒体，零推理、零写入。
   - 本机试跑一次只跑一个场景，最长 60 秒；不写 ORM、不存快照、不调 VLM、不通知。
3. 前端只消费规范化 `PackCard` / `PackDetail`，不通过 `cameras === null` 判断包格式。
4. 核心规则 YAML 是运行参数事实；manifest 只补产品内容、机位指导和体验资产，不能重复维护阈值。
5. 应用前必须生成 `ApplyPlan`；Web 回传 `expected_fingerprint` 后才应用，内容变化返回 409。
6. 新流程创建的规则默认禁用，逐路完成校准后明确启用；摄像头继续默认 stopped。
7. 新增轻量 `PackDeployment` 与资源映射，不新增门店/工单模型。
8. 三个深模块承担复杂度：`PackCatalog`、`PackExperience`、`PackDeployment`；HTTP、Web、CLI 只使用其稳定接口。
9. 旧 HTTP/CLI apply 请求保持兼容；Web 新流程必须走 plan + fingerprint + 配置后启用。

## 3. 范围

### 3.1 本期包含

- manifest v2 的产品内容、机位指导、体验资产与事件样例合同。
- 旧包规范化和缺失内容降级。
- 方案详情与白名单资产 REST 路由。
- 市场卡片重构与独立详情页。
- 快餐店四路浏览器演示资产和可触发本机试跑的匿名化/合成源。
- `PackExperience` 单场景隔离试跑。
- `PackDeployment`、应用计划、原子应用、资源归属和激活清单。
- 四个内置包的业务文案；没有完整媒体的旧包显示海报/文字降级。
- 中文用户文案、响应式、键盘和减少动态效果支持。
- OpenAPI、README、迁移、后端/前端/端到端测试。

### 3.2 本期不包含

- 在线支付、评分、评论、安装量、作者主页、商业授权。
- 门店、机位角色、部署工单、跨摄像头 Re-ID。
- 多路并行实时体验。
- 体验中的 VLM、通知、正式事件、快照或统计写入。
- 方案包脚本执行、远程视频代理或用户画面上传平台。
- 方案版本自动升级和回滚 UI；本期只记录版本与内容指纹，为后续留事实。

## 4. 目标领域合同

### 4.1 规范化详情

`PackDetail` 至少包含：

- `id/name/version/vertical/author/origin/fingerprint`
- `availability` 与兼容状态、阻断原因
- `presentation.tagline/outcomes/requirements/limitations`
- `cameras[]`：机位 id、名称、用途、安装指导、海报、规则
- `rules[]`：稳定 id、业务名称、类型、关键参数摘要、cooldown、intent
- `experience.scenes[]`：原始媒体、结果媒体、poster、事件时间线、trial source 与可用状态
- `application.mode`：`create_cameras` 或 `existing_camera`
- `application.camera_count/rule_count/auto_start/warnings`
- `privacy.processing=local`、`uploads_frames=false`
- 安全清洗后的说明文档

旧格式包规范化为一个虚拟机位，应用模式为 `existing_camera`；前端不得感知 manifest 版本差异。

### 4.2 manifest v2

- 新增可选 `format_version`、`presentation`、`cameras[].purpose/placement/poster`、`experience.scenes[]`。
- `input_preview`、`result_preview`、`poster`、`events`、`trial_source` 均为包根目录内相对路径。
- 规则稳定 id 使用 YAML 文件 stem；manifest 不重复声明规则阈值。
- 展示资产错误只让对应场景降级；核心 manifest/rules 错误让包进入 `invalid`，但市场仍能显示不可用条目和错误原因。

### 4.3 部署记录

新增 Alembic 表：

```text
pack_deployments
  id, pack_id, pack_version, pack_digest,
  status(configuring|active|degraded), created_at, updated_at

pack_deployment_resources
  id, deployment_id, camera_slot_id,
  kind(camera|rule|video), resource_id,
  ownership(created|bound), configured
```

约束：

- 迁移幂等，升级前备份/失败回滚继续走现有迁移体系。
- 卸载包不级联删除部署资源。
- 删除/缺失资源时部署状态可计算或修正为 `degraded`。
- 不靠摄像头名称推断归属。

## 5. REST 路由与错误合同

新增：

```text
GET    /api/packs/{pack_id}
GET    /api/packs/{pack_id}/assets/{asset_id}
POST   /api/packs/{pack_id}/trials
GET    /api/pack-trials/{trial_id}
GET    /api/pack-trials/{trial_id}/live.mjpg
DELETE /api/pack-trials/{trial_id}
POST   /api/packs/{pack_id}/apply-plan
GET    /api/pack-deployments/{deployment_id}
PATCH  /api/pack-deployments/{deployment_id}/resources/{resource_id}
```

扩展现有：

```text
POST /api/packs/{pack_id}/apply
```

Web 请求增加 `expected_fingerprint`；兼容旧调用可省略。

关键错误：

- 404：包/场景/资产/部署不存在。
- 409：包内容变化、已有试跑、场景不可试跑、资源状态不允许。
- 410：试跑已过期。
- 416：媒体 Range 非法。
- 422：目标摄像头缺失/不允许、manifest 或绑定非法。
- 503：检测器或体验源不可用；预渲染演示仍可观看。
- 507：应用所需磁盘空间不足。

## 6. 全局验收标准

### 6.1 详情与内容

- [ ] 市场卡片主入口为“查看详情”，一击进入可分享、可刷新的 `/marketplace/{id}`。
- [ ] 详情使用业务语言展示能解决什么、摄像头怎么装、会产生什么结果、有哪些限制。
- [ ] 快餐店详情显示门口/点餐/后厨/店内四路机位与正确规则映射。
- [ ] 页面不承诺当前模型不具备的身份、工服、跨摄像头识别能力。
- [ ] 旧餐饮/零售/美发包使用同一详情模型；缺媒体时明确降级，不显示“旧格式”。
- [ ] 不兼容或无效包不静默消失，可查看原因但不能体验/应用。

### 6.2 效果演示

- [ ] 快餐四个场景都有浏览器可播放的原始/结果媒体和 poster。
- [ ] 结果媒体可见目标框、ROI/计数线、触发状态与结果，不是灰底线框占位片。
- [ ] 用户可切换机位、原始/结果画面、点击事件跳到对应时刻。
- [ ] 媒体不入详情 JSON；资产路由支持 Range、MIME、ETag 和 inline 播放。
- [ ] 无媒体或编码不支持时回退海报/文字，不出现空白播放器。

### 6.3 本机试跑

- [ ] 只运行当前一个场景，默认 60 秒，全局最多一个主动试跑。
- [ ] 支持包内源、视频库、已运行摄像头三种来源；运行摄像头只复用帧，不重启。
- [ ] 显示本机画面、检测框、规则状态、临时命中时间线、实际处理帧率。
- [ ] stop 幂等；停止、过期、异常和服务关闭都释放资源。
- [ ] 试跑前后 Camera/Rule/Event/EventAction/Video 数量和快照目录不变。
- [ ] 试跑不调用 VLM、Notifier，不产生出站画面请求。
- [ ] 现有灰底源在替换为可触发目标前不得标记为可试跑。

### 6.4 应用与激活

- [ ] 点击应用先展示服务端计算的摄像头、规则、视频变更和后续步骤。
- [ ] 新包不选外部 camera；旧包必须由用户明确选择，不默认第一台。
- [ ] 内容指纹变化后 apply 返回 409 并要求重新确认。
- [ ] 任一步失败时 DB 行和复制文件全部回滚，不留半套资源。
- [ ] 应用成功创建部署记录和资源映射；摄像头 stopped，规则 disabled/待校准。
- [ ] 结果页逐路引导换源、看画面、校准、启用、启动和验证。
- [ ] 离开后可通过 deployment id 继续配置；资源缺失显示 `degraded`。
- [ ] 旧 HTTP/CLI apply 行为有兼容测试，现有用户脚本不因新增字段失效。

### 6.5 安全、数据与升级

- [ ] 资产 id 不暴露绝对路径；`..`、绝对路径、编码斜杠、越界 symlink 均拒绝。
- [ ] README 禁止 raw HTML 或经严格允许列表清洗；脚本、事件属性、危险 URL 被过滤。
- [ ] URL 安装限制协议、重定向、超时、下载大小，拒绝环回/私网/本机文件地址。
- [ ] ZIP 解压拒绝路径穿越、symlink、成员/体积超限和解压炸弹。
- [ ] prompt 未接入运行时前不得显示为“已启用 AI 复核”。
- [ ] 表结构变更有幂等 Alembic 迁移和 `tests/test_upgrade.py` 覆盖。
- [ ] API 变化后 `docs/openapi.json` 更新；全量 `make test` 通过。

### 6.6 Web 质量

- [ ] 5173 开发环境和 8600 单端口均支持详情直达、刷新、返回、复制链接。
- [ ] 桌面/移动布局可用；视频控制、机位切换、事件跳转支持键盘。
- [ ] `prefers-reduced-motion` 下不自动播放/循环。
- [ ] 用户可见内容为中文；技术类型只放次级信息。

## 7. 子任务拆解与阶段

### Stage 1：合同与内容基础（可并行）

#### Task 1 — Catalog、manifest v2 与详情/资产接口

建议 assignee：`rd-cursor`

主要文件：

- `opencam/packs/manifest.py`
- `opencam/packs/catalog.py`（新建）
- `opencam/packs/installer.py`
- `opencam/api/packs.py`
- `opencam/models.py`（仅 Pydantic 详情输出，不改 ORM）
- `tests/test_packs.py`
- 新增详情/资产安全测试

DoD：

- manifest v2 与旧包规范化通过模块接口测试。
- `GET detail`、资产 Range/MIME/ETag、路径与 Markdown 安全测试全绿。
- 列表不读媒体/跑 OpenCV，详情不内嵌媒体字节。
- 无效/不兼容包可发现但禁用。
- 不修改现有 apply 行为。

#### Task 2 — 内置包产品内容与体验资产

建议 assignee：`rd-kimi`

主要文件：

- `packs/fast-food/**`
- `packs/restaurant/**`
- `packs/retail-chain/**`
- `packs/salon/**`
- `scripts/` 下体验资产生成/校验脚本
- 媒体内容合同测试

DoD：

- 四包补业务 outcome、requirements、limitations、机位/应用说明。
- 快餐四路各有 input/result/poster/events/trial source。
- 浏览器媒体为 H.264/yuv420p MP4 或兼容 WebM；单场景 8–20 秒，尺寸受控。
- 四个 trial source 可由当前 detector + rule 重放得到声明结果；测试不依赖网络下载。
- 文案不超出真实能力；其他三包无视频时使用明确降级内容。

### Stage 2：详情体验（Task 1、2 合并后并行）

#### Task 3 — 市场卡片与独立详情页

建议 assignee：`rd-kimi`

主要文件：

- `web/src/views/marketplace.tsx`
- `web/src/views/pack-detail.tsx`（新建）
- `web/src/lib/packs.ts`
- `web/src/app/[[...slug]]/client.tsx`
- 前端测试

DoD：

- 卡片发现、详情 Hero、业务结果、机位卡、效果工作台、限制和应用影响完整。
- 原始/结果切换、事件跳转、媒体降级、加载/404/不兼容状态可用。
- 新旧包差异不泄漏为前端格式判断。
- 详情直达/刷新/返回和静态导出测试全绿。
- 响应式、键盘、减少动态效果和中文文案验收通过。

#### Task 4 — PackExperience 隔离试跑

建议 assignee：`rd-cursor`

主要文件：

- `opencam/packs/experience.py`（新建）
- `opencam/api/packs.py`
- 体验 runner/REST 测试

DoD：

- `start/inspect/stop` 深模块接口与四条清理路径完成。
- 包内源、视频库、运行摄像头三种来源完成。
- 单会话、60 秒、TTL、全局推理锁和 MJPEG/状态接口完成。
- 试跑无 DB/快照/VLM/通知副作用，有自动化断言。
- detector 不可用时返回 503，详情预渲染不受影响。

### Stage 3：原子应用与部署追踪

#### Task 5 — PackDeployment、迁移、plan/apply 与激活状态

建议 assignee：`rd-cursor`

主要文件：

- `opencam/models.py`
- `opencam/migrations/versions/` 新迁移
- `opencam/packs/deployment.py`（新建）
- `opencam/packs/apply.py`
- `opencam/api/packs.py`
- `tests/test_packs.py`
- `tests/test_upgrade.py`
- `tests/test_cli.py`

DoD：

- `plan/apply` 接口、新旧包双轨、fingerprint 409 完成。
- DB + 文件 staging/补偿保证全成功或全失败。
- 部署与资源映射迁移幂等；升级/回滚测试全绿。
- 新流程规则 disabled、配置后启用；部署状态可恢复且资源缺失 degraded。
- 现有 CLI/HTTP apply 兼容。

### Stage 4：完整闭环与总验收

#### Task 6 — Web 试跑、应用确认、部署激活与集成验收

建议 assignee：`rd-kimi`

主要文件：

- `web/src/views/pack-detail.tsx`
- 新增试跑/应用/部署 UI 模块
- Web 测试
- `README.md`
- `docs/openapi.json`（脚本生成）
- 端到端测试

DoD：

- 本机试跑、变更预览、确认、结果页、跨会话激活清单全部可操作。
- 快餐完整主路径与旧包路径测试通过。
- `uv run python scripts/export_openapi.py` 已执行。
- `make test`、前端测试和 `make serve` 构建/直达检查全绿。
- README 说明详情、两级体验、应用后校准与数据边界。

## 8. 合并与巡检规则

- Stage 1 的两个 PR 均合并后才解锁 Stage 2。
- Stage 2 的两个 PR 均合并后才解锁 Stage 3。
- Stage 3 合并后解锁 Stage 4。
- 每个子任务 PR 标题包含主任务编号，正文包含 `Closes <子任务编号>`。
- 禁止直接 push `main`；代码只能经 PR 合并。
- `patrol.auto_merge=true` 时，巡检器只在对应 DoD、CI 和本地必要验证全部通过后合并。
- 任何 schema 改动必须先生成并人工检查迁移；不允许运行时手写 ALTER。

## 9. 计划确认后的 Loop It 动作

1. 只提交并推送本计划与对应产品方案文档，不暂存工作区其他改动。
2. 创建 Multica 主任务“解决方案包详情与体验”。
3. 按 Stage 创建 6 个子任务，标题统一以前缀 `[<主任务编号>]` 开头。
4. Stage 1 两个任务置 `todo`；Stage 2–4 置 `backlog`。
5. 主任务 metadata：
   - `plan_path=docs/superpowers/plans/2026-08-20-solution-pack-detail-experience.md`
   - `loop_it_phase=executing`
6. 不创建单任务 Autopilot，由 workspace 唯一 `Loop Patrol` 接管巡检。
