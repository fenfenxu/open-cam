# 升级与数据安全设计

> 面向开发者/Agent 的技术设计文档。约定清单见 `AGENTS.md`「升级与数据安全」，本文讲清"为什么这么做"和"具体怎么运转"。

## 目标

open-cam 装在本机持续迭代，升级程序版本时必须保证：

1. 用户本地的 SQLite 库与快照文件**不被清除、不被弄坏**；
2. 任意历史版本的数据库可以**一路直升到最新**（跨版本升级）；
3. 升级过程**可验证**：升级前备份、升级后质检、失败自动回滚；
4. 升级失败时**拒绝带病启动**，并给出可诊断的错误。

## 总体思路

```
数据与代码分离  +  schema 版本化迁移(Alembic)  +  迁移前备份/失败回滚  +  升级质检
```

四者缺一不可：分离保证"升级程序不动数据"；版本化保证"跨版本可升"；备份回滚是兜底；质检让问题在启动时暴露而不是运行期爆炸。

## 1. 数据与代码分离

- 默认数据目录用 `platformdirs` 落到用户级目录（macOS `~/Library/Application Support/open-cam`，Linux `~/.local/share/open-cam`），见 `config.default_data_dir()`。**升级只替换程序，数据目录不动。**
- 可用 `config.yaml` 的 `data_dir` 或环境变量 `OPENCAM_DATA_DIR` 覆盖。
- 旧版本数据在仓库 `./data`：`config.migrate_legacy_data_dir()` 在首次启动时把 `opencam.db` 和 `snapshots/` **复制**到新目录（不删旧目录，用户确认无误后自行清理）。
- 只有在使用默认数据目录、新目录尚无库文件时才触发搬迁，显式配置过 `data_dir` 的环境不受打扰。

## 2. Schema 版本化迁移（Alembic）

代码结构：

```
opencam/migrations/
├── __init__.py     运行时迁移 API：ensure_schema / stamp / upgrade_head / backup / verify_schema
├── env.py          Alembic env：运行时复用 init_db 注入的连接；CLI 模式回退 settings.db_url
├── script.py.mako  版本脚本模板
└── versions/       版本链：0001 基线 → 0002 events.source_offset → 0003 处置闭环 → 0004 videos 表
alembic.ini         仅开发期 `make revision` 用；运行时不读它
```

### init_db 的三种情形（`migrations.ensure_schema`）

| 库状态 | 判定 | 处理 |
|---|---|---|
| 全新库 | 一张表都没有 | `Base.metadata.create_all` 直接建全表 → `stamp head` |
| ≤0.2.x 存量库 | 有表但无 `alembic_version` | `_legacy_fixup`（补 `rules.name` 列 + 中文名兜底 + 补建缺失表）→ `stamp 0001` → upgrade 链升到 head |
| 已接入版本化的库 | 有 `alembic_version` 且 < head | 备份 → upgrade → 质检 |

要点：

- **`_legacy_fixup` 是冻结的历史包袱**：只服务 Alembic 引入前的存量库，职责仅限"基线 0001 已含但无版本脚本负责"的 `rules.name`。之后的所有变更一律写新版本脚本。
- **版本脚本必须幂等**：upgrade 前用 `sa.inspect` 判断表/列是否存在（参照 0002/0003/0004），因为存量库可能已被 `_legacy_fixup` 或旧手写补丁补过部分结构。
- **破坏性变更分两步走**：先发"加新列/新表 + 双写/回填"，隔一个版本再"删旧的"。绝不在一个版本里又改结构又毁旧数据。
- 新增迁移：`make revision m="说明"`（底层是 `alembic revision --autogenerate`），生成后**人工 review** 再入库。

## 3. 迁移前备份与失败回滚

- 仅当**真的发生版本变化**（current ≠ head）才备份：把 SQLite 文件复制到 `data_dir/backups/opencam-v{原版本}-{时间戳}.db`。全新建库、版本已是最新都不产生备份。
- 升级过程中任何异常（迁移脚本抛错、升级后质检不过）→ `engine.dispose()` 断开连接 → 用备份文件覆盖还原 → 异常继续上抛，服务拒绝启动。
- 内存库（`:memory:`）跳过备份。
- 真实案例：0003 版本漏了 `videos` 表的迁移脚本，存量库升级后被质检拦下，自动回滚保住了库；随后补 0004 修复。备份与回滚在真实数据上验证过（1882 事件、5 摄像头，升级后一行不丢）。

## 4. 升级质检（doctor）

`opencam/doctor.py` 两个入口：

- **启动自检** `verify_startup()`：lifespan 里 `init_db` 之后跑 `verify_schema`——`PRAGMA integrity_check`、必备表/列齐全（取自 `Base.metadata`，缺列会提示 `make revision`）、`alembic_version == head`。不合格直接抛异常，**fail fast 拒绝启动**。
- **运行期全量检查** `check_health()`：`GET /api/system/health`（全过 200，否则 503 + 明细）或 `opencam system doctor`（不过则退出码 1）。检查项：
  - schema：版本、完整性；
  - 目录：data_dir / snapshot_dir 存在且可写（临时文件探测）；
  - 快照：抽查最近 5 条带快照的事件，文件是否还在盘上。

升级后的标准动作：启动服务 → `opencam system doctor` 全绿 → 完事。

## 5. 快照路径：相对存储 + 兼容读取

- 新数据只存**相对 data_dir 的路径**（`snapshots/xxx.jpg`，见 `pipeline._save_snapshot`），数据目录整体搬迁后仍然有效。
- 读取统一走 `config.resolve_snapshot_path()`，兼容三种历史格式：
  - 相对 data_dir（新格式）；
  - 绝对路径（≤0.2.x）；
  - 相对仓库根目录的 CWD 路径（`data/snapshots/xxx.jpg`，剥掉 `data/` 前缀再解析）。
- `GET /events/{id}/snapshot` 拒绝含 `..` 的路径穿越。

## 6. 测试方案

全部固化成可执行脚本 `tests/test_upgrade.py`（tmp_path 临时库，不碰真实数据与模型）：

| 用例 | 验证点 |
|---|---|
| `test_fresh_db_stamped_at_head` | 全新库建表 + stamp head，不产生备份 |
| `test_legacy_db_adopted_and_data_preserved` | 手工 SQL 造 0.2.x 旧库 → 接入版本化 → 旧数据一行不丢、缺列缺表补齐 |
| `test_upgrade_creates_backup_and_preserves_data` | 临时迁移目录模拟"新版本带新脚本"→ 升级成功、数据在、备份文件生成 |
| `test_failed_upgrade_rolls_back` | 迁移脚本抛错 → 自动还原，版本/数据/结构回到升级前 |
| `test_migrate_legacy_data_dir` | 旧 `./data` 复制到用户目录，旧目录保留，不重复搬迁 |
| `test_health_endpoint_all_ok` | 质检 API 全绿 |
| `test_snapshot_path_relative_and_legacy_absolute` | 三种路径格式可读、`..` 穿越 404、文件丢失 404 |

技巧：跨版本升级/回滚不依赖仓库里真实存在"下一个版本"，而是**在 tmp 目录造一个假的 migrations 目录**（以真实 head 为 down_revision 加一个 `testnext` 脚本），通过 `ensure_schema(..., script_location=...)` 注入——这样测试不随版本链增长而失效。

发版纪律：`make test` 全绿 + `make openapi` 更新快照。

## 7. 一次升级（发版）的完整流程

```
开发者改 models.py
  → make revision m="..."        # 生成幂等的版本脚本，人工 review
  → 补 tests/test_upgrade.py 用例（如涉及迁移行为）
  → make test && make openapi    # 全绿后提交
用户侧升级（替换程序后首次启动）
  → lifespan: 旧数据目录搬迁（仅首次）→ ensure_schema（备份 → 迁移 → 质检）
  → verify_startup 自检，不合格拒绝启动
  → 运行期 opencam system doctor 复核
```
