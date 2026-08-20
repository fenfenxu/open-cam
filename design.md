---
version: alpha
name: open-cam 控制台
description: 本地视频监控分析与事件处置控制台的视觉、布局和组件决策基线
colors:
  primary: "oklch(0.205 0 0)"
  on-primary: "oklch(0.985 0 0)"
  secondary: "oklch(0.97 0 0)"
  on-secondary: "oklch(0.205 0 0)"
  neutral: "oklch(1 0 0)"
  on-neutral: "oklch(0.145 0 0)"
  surface-muted: "oklch(0.97 0 0)"
  on-surface-muted: "oklch(0.556 0 0)"
  border: "oklch(0.922 0 0)"
  focus: "oklch(0.708 0 0)"
  destructive: "oklch(0.577 0.245 27.325)"
  on-destructive: "oklch(0.985 0 0)"
typography:
  page-title:
    fontFamily: Geist Variable
    fontSize: 1.5rem
    fontWeight: 500
    lineHeight: 2rem
  section-title:
    fontFamily: Geist Variable
    fontSize: 1rem
    fontWeight: 500
    lineHeight: 1.5rem
  body:
    fontFamily: Geist Variable
    fontSize: 0.875rem
    fontWeight: 400
    lineHeight: 1.25rem
  label:
    fontFamily: Geist Variable
    fontSize: 0.875rem
    fontWeight: 500
    lineHeight: 1.25rem
  caption:
    fontFamily: Geist Variable
    fontSize: 0.75rem
    fontWeight: 400
    lineHeight: 1.125rem
rounded:
  none: 0px
  sm: 0.375rem
  md: 0.5rem
  lg: 0.625rem
  full: 9999px
spacing:
  hairline: 1px
  xs: 0.25rem
  sm: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  2xl: 2rem
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    height: 2rem
    padding: 0.625rem
  button-secondary:
    backgroundColor: "{colors.secondary}"
    textColor: "{colors.on-secondary}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    height: 2rem
    padding: 0.625rem
  button-destructive:
    backgroundColor: "{colors.destructive}"
    textColor: "{colors.on-destructive}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    height: 2rem
    padding: 0.625rem
  filter-select:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.on-neutral}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    height: 2.25rem
    padding: 0.75rem
  filter-select-muted:
    backgroundColor: "{colors.surface-muted}"
    textColor: "{colors.on-surface-muted}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    height: 2.25rem
    padding: 0.75rem
  table:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.on-neutral}"
    typography: "{typography.body}"
    rounded: "{rounded.md}"
    padding: 1rem
  status-badge:
    typography: "{typography.caption}"
    rounded: "{rounded.full}"
    padding: 0.5rem
  detail-drawer:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.on-neutral}"
    typography: "{typography.body}"
    rounded: "{rounded.lg}"
    padding: 1.5rem
---

## Overview

open-cam 是一个面向值班和处置的本地视频监控分析控制台。它服务于“发现事件、理解证据、判断 AI 结果、完成处置、回看配置”这一连续任务，而不是装饰型大屏或普通 CRUD 后台。

整体气质是克制、可靠、专业、偏中性。界面以高对比黑白灰为基础，强调色只服务于主要动作、当前选择和需要注意的状态。信息密度可以较高，但必须稳定对齐、层级清楚、容易扫描；视频和快照证据优先于装饰。

本文件有两层作用：

1. YAML front matter 是可供 agent 和工具消费的规范 token。
2. Markdown 正文是这些 token 的使用理由，以及跨页面必须统一的组件决策。

页面实现的优先级固定为：可理解性 > 可扫描性 > 可操作性 > 信息密度 > 装饰性。

## Colors

颜色使用黑白灰的单一中性体系，避免每个页面自行选择品牌色。所有 CSS 应通过 web/src/index.css 中的语义变量落地，新增颜色必须先变成语义 token。

- Primary 使用深色，服务于主要动作和主要交互，不用于装饰。
- Secondary 使用浅灰，服务于次要按钮和次级控件。
- Neutral 是页面和内容层的白色基础，on-neutral 是主文字。
- Surface muted 和 on-surface-muted 用于辅助说明、禁用态、空态和次级信息。
- Destructive 只用于危险动作和确实需要用户注意的失败，不把所有问题都染成红色。
- Focus 是键盘焦点的专用强调色，在浅色和深色主题中都要可见。
- success、warning、info 如果业务需要，先补充语义变量并同时提供文字或图标；不能在页面里临时写颜色。

普通文字目标达到 WCAG AA 对比度，状态不能只由颜色表达。颜色必须与文字、图标、边框、位置或结构一起工作。

## Typography

字体使用 Geist Variable；中文使用系统 sans-serif 回退。页面标题用于说明当前任务，区块标题用于组织内容，正文用于常规信息，caption 用于时间、来源和辅助说明。

- 页面标题：1.5rem / 2rem / 500。
- 区块标题：1rem / 1.5rem / 500。
- 正文与表格数据：0.875rem / 1.25rem / 400。
- 标签与动作：0.875rem / 1.25rem / 500。
- 辅助信息：0.75rem / 1.125rem / 400。

用户可见内容使用中文。后端枚举、内部 id、API key 和英文技术状态必须通过统一映射后再展示。

## Layout

页面采用 AppShell → PageHeader → 页面说明和主要动作 → 筛选或工具栏 → 主内容 → 详情层和反馈层的结构。

使用 4px 基础间距节奏，常用间距为 8px、12px、16px、24px、32px。桌面内容区保持稳定的最大宽度和留白；筛选栏允许换行；中等宽度隐藏次要表格列或转入详情；手机端将横向筛选改为纵向布局或统一筛选抽屉。

筛选、排序、批量动作和结果数量必须靠近它们影响的列表。主要动作放在 PageHeader，列表级动作放在列表工具栏。不要在页面内另外发明第二套侧边栏、顶部导航或固定操作条。

所有视频、快照和规则画布默认保持原始比例并使用 contain 思路展示，不能拉伸证据，也不能让装饰性叠加遮挡关键内容。

## Elevation & Depth

界面优先使用背景层、卡片层、边框和间距建立层级，避免厚重阴影和持续发光。弹层可以使用现有组件的阴影来建立浮层关系，但阴影不承担状态含义。

页面底色、内容卡片、popover、抽屉和对话框要有清晰层级。复杂列表依靠表头、列对齐、分隔线和留白组织，不依赖大量卡片堆叠。

## Shapes

形状语言是轻微圆角、稳定边界、紧凑但不拥挤。控件和卡片统一沿用现有 radius token：小圆角用于紧凑控件，中圆角用于表格和内容容器，大圆角用于按钮、筛选器和抽屉，full 只用于状态徽标等胶囊。

同一个区域不得混用明显不同的圆角、边框粗细或控件高度。交互控件必须有可见 hover、focus、disabled 和 active 状态；动效服务于状态变化，不使用持续闪烁或会干扰监控内容的强动画。

## Components

### 公共组件唯一入口

| 需求 | 默认实现 | 代码位置 |
| --- | --- | --- |
| 按钮 | Button | web/src/components/ui/button.tsx |
| 文本输入与标签 | Input + Label | web/src/components/ui/input.tsx、label.tsx |
| 单选下拉 | Select | web/src/components/ui/select.tsx |
| 列表横向筛选 | LabeledSelect | web/src/components/app/labeled-select.tsx |
| 复选框 | Checkbox | web/src/components/ui/checkbox.tsx |
| 表格 | DataTable + Table | web/src/components/app/data-table.tsx、ui/table.tsx |
| 页面标题 | PageHeader | web/src/components/app/page-header.tsx |
| 详情侧栏 | DetailDrawer | web/src/components/app/detail-drawer.tsx |
| 规则画布 | RuleCanvas | web/src/components/app/rule-canvas.tsx |
| 视频墙 | VideoWall | web/src/components/app/video-wall.tsx |
| 状态文字映射 | labels | web/src/lib/labels.ts |
| 短消息 | sonner | web/src/components/ui/sonner.tsx |

如果需求已在表中有入口，页面不得另选同类实现。要扩展能力时优先修改公共入口并补测试。

### 筛选器

列表页顶部的单选筛选统一使用 LabeledSelect。触发器显示“字段名：当前值”，例如“摄像头：[1] 演示摄像头”“处置状态：徘徊逗留”；选中后不能只剩一个孤立值。

必须提供明确的默认值“全部摄像头”“全部状态”等。全部是明确的筛选值，不是散落的空字符串技巧。摄像头选项使用稳定的“[id] 名称”，名称重复时仍可区分。筛选改变后立即生效，当前值在触发器和菜单中都可辨认。

筛选栏必须有可见的“重置筛选”按钮。无激活条件时按钮仍保留但禁用；点击后恢复全部默认值。筛选栏允许换行，窄屏改为纵向布局或统一的筛选抽屉。

禁止使用裸 Select 作为列表筛选，禁止每个页面实现 FilterSelect、StatusFilter 等同类私有组件，禁止只提供清空单项而没有全局重置入口，也禁止用颜色、仅图标或 tooltip 表达当前筛选。

多选不是 LabeledSelect 的默认能力。确实需要多选时，优先使用 DropdownMenuCheckboxItem 组合出公共 LabeledMultiSelect；只有需求明确且至少有两个页面可复用时才新增，并同步更新本文件和测试。

### 步骤条

训练、方案安装和复杂配置统一使用语义化 nav、ol、li 步骤条。训练页现有步骤实现是参考实现；第二个步骤流出现前，应评估提取公共 StepProgress，不得复制页面 JSX 后改文案。

步骤条必须同时显示序号和中文名称；当前步骤使用 aria-current="step"，同一时间只能有一个当前步骤。已完成、当前、未开始、失败或需修正要有文字或图标差异，不能只靠颜色。

未满足前置条件的后续步骤禁用；已完成步骤只有能恢复对应状态时才允许回退。步骤跳转不得无提示丢失输入。窄屏可以横向滚动，但不能隐藏当前步骤名称，主体还要提供“上一步”“下一步”等明确动作。

不能用纯数字 tabs、进度条或面包屑冒充步骤条，也不能每页自行选择步骤的颜色、形状、连接线和点击规则。

### 按钮

统一使用 Button。一个区域通常只有一个主要动作，其余使用 secondary、outline、ghost 或 destructive。动作文案使用“动词 + 对象”，例如“确认处置”“保存配置”“重置筛选”；避免无对象的“确定”“提交”“处理”。

危险动作使用 destructive，并在 Dialog 中说明后果、提供取消和明确确认。加载时禁用重复提交但保留动作名称。图标按钮必须有 aria-label，必要时加 title。

### 页面头部、表格和详情

页面头部统一使用 PageHeader。列表默认使用 DataTable；页面只提供列和数据，不重复造表格空态、加载态、错误态和结构。

行操作放在操作列或详情抽屉，不把整行点击作为唯一入口。整行可点击时必须有键盘等价操作、明显 focus 和可预测的详情打开方式。

查看详情、补充备注、确认处置等上下文任务优先使用 DetailDrawer。删除、忽略、停止、部署、回滚等需要用户作决定的动作使用 Dialog。弹层必须有标题、关闭方式、焦点管理和 Escape 行为。

### 状态、反馈和表单

状态展示通过 web/src/lib/labels.ts 映射中文，禁止直接渲染 open、acked、resolved、pending 等 key。事件处置状态、VLM 处理状态、VLM 判定、摄像头运行状态和训练任务状态是不同维度，不能共用一个状态文案。

Badge 只表达状态，不承担点击动作。状态颜色必须配合文字、图标或结构。“已确认”是状态，“确认”是动作；“误报”是判定，“忽略”是处置动作。

sonner 只用于短暂、非阻塞的成功或信息反馈。重要错误、字段错误、权限问题、模型不可用和影响后续操作的结果使用页面内联错误或横幅，并说明原因和下一步。可恢复错误提供重试，配置错误提供去设置或修正配置的路径。

每个表单控件都有可见 Label，并通过 htmlFor/id 关联。placeholder 只提供示例，不承载字段名、必填提示或错误信息。提交失败时保留输入值并定位可修正字段。

## Do's and Don'ts

### Do

- 先读取本文件，再读取目标页面和相关公共组件。
- 用 rg 搜索已有模式，优先复用公共组件。
- 让选中值、状态、图标和空态脱离上下文也能被理解。
- 为加载、空数据、错误、成功、禁用、键盘和焦点提供完整状态。
- 视觉改动使用 web/src/index.css 的语义 token。
- 交互改动补充测试，并在交付前运行相关 Vitest、TypeScript 和 git diff --check。

### Don't

- 不要按个人审美在每个页面重新选择筛选器、步骤条、按钮、徽标、表格或弹层。
- 不要在页面中直接写新的颜色、圆角、阴影、状态映射或同类私有组件。
- 不要只用颜色、数字、图标或 tooltip 传达关键语义。
- 不要把 AI 判定伪装成视频事实或人工事实。
- 不要用 Toast 替代重要错误、确认决策或需要用户采取行动的内容。

### 例外记录

只有现有组件无法表达新的数据类型或交互、业务流程确实不同、平台或无障碍要求使默认实现不可行，或确有可复用的公共能力缺失时，才允许例外。

例外必须记录问题、现有组件为何不够、选择的实现、影响范围、是否应抽为公共组件以及测试方式。个人偏好和“这个页面想更特别”不构成例外理由。新的默认模式必须先回写本文件，再推广到页面。
