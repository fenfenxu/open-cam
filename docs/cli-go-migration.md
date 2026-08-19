# CLI 技术选型与 Go 迁移计划

> 决策记录，2026-08-19。

## 现状

`opencam` CLI 是 Python 实现（`opencam/cli.py`，argparse + httpx），作为后端包的 entry point 分发（`uv tool install`）。

## 结论

**现阶段保持 Python，CLI 转为面向客户分发时用 Go 重写。**

## 理由

- CLI 是 Open API 的薄封装，语言影响小，**分发方式才是关键**
- Python 版当前零额外维护成本：同仓库同语言、API 变更同步快、目标环境（装了 open-cam 的机器 / AI Agent）必有 Python
- Python 分发给无 Python 环境的企业客户不体面（要装运行时，或 PyInstaller 打包体积大、易误报）
- Go 编译出单个静态二进制，下载即用，是此类工具的最优解

## 为什么是 Go 而不是 Node（既然走 npm 分发，用户机器就有 Node）

关键区分：**npm 只是分发渠道，不是运行时承诺**。有 Node ≠ 有对的 Node。

- **启动速度**：Agent 是 CLI 的主要调用者，一个任务可能连续调几十上百次。Node 冷启动 50–100ms+ 还要加载依赖树，Go 二进制 5–10ms，累计差距用户可感。
- **密封性**：Go 静态二进制不依赖用户机器上的任何东西；npm 包里约 20 行 JS 启动器在任何 Node 版本上都能跑，兼容性风险约等于零。用 Node 写则继承用户的 Node 版本地狱（14 还是 22、ESM/CJS、依赖装不装得上）。
- **依赖供应链**：Node CLI 拖着 node_modules 依赖树，间接依赖挂了/被投毒都会砸到自己；Go 把依赖固化进二进制，装完就是单个文件，企业安全审计友好。
- **并发**：WebSocket 事件订阅、批量并发 API 调用等场景 goroutine 更省心（次要加分项）。
- **渠道可换**：Go 二进制是资产，npm 壳、安装包、Homebrew、Releases 直链都只是渠道，随时可换，代码一行不动。

## 参照

飞书官方 lark-cli 即 Go 实现（通过 npm 包 `@larksuite/cli` 分发二进制），并配套 AI Agent Skills——与我们"CLI + Skill"的架构一致。重写时可参考其命令组织与 Skill 封装方式。

npm 分发 Go 二进制是成熟做法（esbuild/swc/Biome 同流派）：各平台编译产物发为平台子包（`optionalDependencies` 按需下载），主包只含一个转发启动器；或用 postinstall 从 Releases 拉取。若目标门店 PC 无 Node 环境，则直接提供 Releases 二进制/安装包，届时按分发渠道定。

## 触发条件（满足其一时启动迁移）

- CLI 要分发给没有 Python 环境的企业客户
- 需要交互式引导（wizard）等重终端体验
- 出现 CLI 独立发布/独立版本节奏的需求

## 迁移约束

- 命令树与输出格式（JSON 契约）保持兼容，Agent Skill 无需修改
- 重写工作量预估 1–2 天（薄封装，逻辑都在服务端）
