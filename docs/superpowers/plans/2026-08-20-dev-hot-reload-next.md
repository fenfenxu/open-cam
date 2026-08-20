# 开发体验对齐 Multica Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 控制台改用 Next.js 16（自带 Dev Overlay），普通 Python 热加载，DDL 经页面确认后才重启执行迁移。

**Architecture:** `make start` 统一启动 FastAPI `:8600` 与 `next dev :5173`（HMR + 左下角 N Issues），并对后端使用 `--reload-dir opencam`、排除 `models.py` / `migrations/`。只启动后端用 `make backend`；单端口用 `make serve`。确认 DDL 时写入 `opencam/_dev_reload.py` 触发一次换进程，lifespan 的 `ensure_schema` 才改库。生产仍静态导出给 FastAPI 单端口，没有 Overlay。

**Tech Stack:** Next.js 16、React 19、Tailwind v4、shadcn/Base UI、TanStack Query、FastAPI、uvicorn `--reload`、Alembic `ensure_schema`。

## Global Constraints

- 规格：`docs/superpowers/specs/2026-08-20-dev-hot-reload-next-design.md`
- 后端保持 FastAPI，不迁 Go；不把 REST 一次性改挂 `/api`
- 不手写 Next Issues 仿品，不引入 `@visulima/vite-overlay`
- 用户可见文案中文；标识符英文；Python `from __future__ import annotations`
- 测试强制 `OPENCAM_DETECTOR=mock`；不碰真实 YOLO / 外网
- 不要把 api key、`auto.crt`、`auto.key`、`tools/mediamtx` 提交进仓库
- 改 API 后必须 `uv run python scripts/export_openapi.py`

---

## 文件地图

- Modify: `opencam/devplaybook.py` — `dev_status()` / `DevStatus` / `RELOAD_SENTINEL`
- Modify: `opencam/api/system.py` — `GET/POST /api/system/dev`
- Modify: `opencam/main.py` — CORS `5173`；静态目录 `web/out`
- Modify: `Makefile` — `--reload-exclude`
- Modify: `.gitignore` — `_dev_reload.py`、`web/.next/`、`web/out/`
- Modify: `web/src/lib/api.ts` — `console.error` + `NEXT_PUBLIC_API_URL`
- Modify: `web/package.json` 及 Next 配置；删除 Vite 入口
- Modify: `web/src/app/` — Next layout；页面改为 App Router
- Modify: `web/src/components/app/app-shell.tsx` — `next/link` + DDL 横幅
- Create: `web/src/components/app/dev-banner.tsx`
- Modify: `tests/test_devplaybook.py`、`tests/test_system_api.py`、`tests/test_web.py`
- Modify: `docs/openapi.json`、`AGENTS.md`、`README.md`

---

### Task 1: `dev_status` 纯函数

**Files:**
- Modify: `opencam/devplaybook.py`
- Test: `tests/test_devplaybook.py`

**Interfaces:**
- Consumes: 现有 `classify(paths) -> list[Hint]`
- Produces:

```python
@dataclass(frozen=True)
class DevStatus:
    reload_on: bool
    state: str  # "idle" | "need_revision" | "need_apply"
    title: str
    detail: str
    steps: tuple[str, ...]
    can_apply: bool

RELOAD_SENTINEL = Path(__file__).resolve().parent / "_dev_reload.py"

def dev_status(
    *,
    reload_on: bool,
    changed_paths: list[str],
    schema_rev: str | None,
    schema_head: str | None,
) -> DevStatus: ...

def write_reload_sentinel() -> Path: ...
```

- [ ] **Step 1: 写失败测试**

在 `tests/test_devplaybook.py` 追加：

```python
from opencam.devplaybook import dev_status, write_reload_sentinel


def test_models_only_is_need_revision_not_apply():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/models.py"],
        schema_rev="0007",
        schema_head="0007",
    )
    assert st.state == "need_revision"
    assert st.can_apply is False
    assert "revision" in st.detail.lower() or "make revision" in " ".join(st.steps)


def test_migration_or_schema_lag_is_need_apply():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/migrations/versions/0008_x.py"],
        schema_rev="0007",
        schema_head="0008",
    )
    assert st.state == "need_apply"
    assert st.can_apply is True


def test_models_plus_migration_can_apply():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/models.py", "opencam/migrations/versions/0008_x.py"],
        schema_rev="0007",
        schema_head="0008",
    )
    assert st.state == "need_apply"
    assert st.can_apply is True


def test_idle_when_only_backend_py():
    st = dev_status(
        reload_on=True,
        changed_paths=["opencam/pipeline.py"],
        schema_rev="0007",
        schema_head="0007",
    )
    assert st.state == "idle"
    assert st.can_apply is False


def test_write_reload_sentinel(tmp_path, monkeypatch):
    import opencam.devplaybook as dp
    target = tmp_path / "_dev_reload.py"
    monkeypatch.setattr(dp, "RELOAD_SENTINEL", target)
    path = write_reload_sentinel()
    assert path == target
    assert "reload_nonce" in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_devplaybook.py::test_models_only_is_need_revision_not_apply -v`

Expected: FAIL（`dev_status` 未定义）

- [ ] **Step 3: 实现**

在 `opencam/devplaybook.py` 增加 `DevStatus`、`RELOAD_SENTINEL`，以及：

```python
def dev_status(
    *,
    reload_on: bool,
    changed_paths: list[str],
    schema_rev: str | None,
    schema_head: str | None,
) -> DevStatus:
    kinds = {h.kind for h in classify(changed_paths)}
    schema_lag = schema_rev != schema_head
    has_migration = "migration" in kinds or schema_lag
    has_ddl_only = "ddl" in kinds and not has_migration
    if has_ddl_only:
        return DevStatus(
            reload_on=reload_on,
            state="need_revision",
            title="表结构已改，还没有迁移脚本",
            detail="只改 models.py 不会建列。请先 make revision 并人工 review，再确认重启。",
            steps=('make revision m="说明"', "review opencam/migrations/versions/", "确认并重启"),
            can_apply=False,
        )
    if has_migration:
        return DevStatus(
            reload_on=reload_on,
            state="need_apply",
            title="待执行数据库迁移",
            detail="确认后将重启进程（摄像头会中断几秒）。启动时 ensure_schema 会备份并执行 DDL。",
            steps=("review 迁移脚本", "确认并重启"),
            can_apply=True,
        )
    return DevStatus(
        reload_on=reload_on,
        state="idle",
        title="热加载已开启" if reload_on else "热加载未开启",
        detail="改 opencam/*.py 会自动换进程。表结构变更不会自动执行。" if reload_on
        else "改后端后请 make restart。",
        steps=(),
        can_apply=False,
    )


def write_reload_sentinel() -> Path:
    RELOAD_SENTINEL.write_text(
        f"# auto-generated; triggers uvicorn --reload\nreload_nonce = {time.time_ns()!r}\n",
        encoding="utf-8",
    )
    return RELOAD_SENTINEL
```

文件顶部增加 `import time`。

- [ ] **Step 4: 跑测试确认通过**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_devplaybook.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add opencam/devplaybook.py tests/test_devplaybook.py
git commit -m "$(cat <<'EOF'
feat: 按改动分类 DDL 确认状态

EOF
)"
```

---

### Task 2: `GET/POST /api/system/dev`、CORS、reload exclude

**Files:**
- Modify: `opencam/api/system.py`
- Modify: `opencam/main.py`（CORS，lifespan 之后、路由之前）
- Modify: `Makefile`（`RELOAD_FLAGS` 加 exclude）
- Modify: `.gitignore`
- Modify: `scripts/dev_status.py`（把 `git_changed_files` 抽到 `devplaybook` 供 API 复用，或 API 直接 import `scripts` 不妥；把 git 列举搬进 `devplaybook.git_changed_files`）
- Test: `tests/test_system_api.py`
- Modify: `docs/openapi.json`（本 task 末尾 export）

**Interfaces:**
- Consumes: `dev_status`、`write_reload_sentinel`、`git_changed_files(repo: Path) -> list[str]`
- Produces: `GET /api/system/dev` JSON 与 `DevStatus` 字段一致（`steps` 为 list）；`POST /api/system/dev/apply` → `{ok: true}` 或 409

- [ ] **Step 1: 写失败测试**

在 `tests/test_system_api.py` 追加：

```python
def test_dev_status_idle(client, monkeypatch):
    monkeypatch.setenv("OPENCAM_RELOAD", "1")
    from opencam import devplaybook as dp
    monkeypatch.setattr(dp, "git_changed_files", lambda _root=None: [])
    resp = client.get("/api/system/dev")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "idle"
    assert body["can_apply"] is False
    assert body["reload_on"] is True


def test_dev_apply_need_revision_409(client, monkeypatch):
    monkeypatch.setenv("OPENCAM_RELOAD", "1")
    from opencam import devplaybook as dp
    monkeypatch.setattr(
        dp, "git_changed_files", lambda _root=None: ["opencam/models.py"]
    )
    resp = client.post("/api/system/dev/apply")
    assert resp.status_code == 409
    assert "revision" in resp.json()["detail"]


def test_dev_apply_writes_sentinel(client, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCAM_RELOAD", "1")
    from opencam import devplaybook as dp
    sentinel = tmp_path / "_dev_reload.py"
    monkeypatch.setattr(dp, "RELOAD_SENTINEL", sentinel)
    monkeypatch.setattr(
        dp, "git_changed_files",
        lambda _root=None: ["opencam/migrations/versions/0008_x.py"],
    )
    monkeypatch.setattr(dp, "head_revision_for_dev", lambda: "0008")
    # schema_rev 由 API 读 DB；tmp_settings 库已是 head。改用 monkeypatch dev_status：
    monkeypatch.setattr(
        dp, "dev_status",
        lambda **kwargs: dp.DevStatus(
            reload_on=True, state="need_apply", title="t", detail="d",
            steps=("s",), can_apply=True,
        ),
    )
    resp = client.post("/api/system/dev/apply")
    assert resp.status_code == 200
    assert sentinel.is_file()


def test_dev_apply_reload_off_409(client, monkeypatch):
    monkeypatch.setenv("OPENCAM_RELOAD", "0")
    from opencam import devplaybook as dp
    monkeypatch.setattr(
        dp, "dev_status",
        lambda **kwargs: dp.DevStatus(
            reload_on=False, state="need_apply", title="t", detail="d",
            steps=("s",), can_apply=True,
        ),
    )
    resp = client.post("/api/system/dev/apply")
    assert resp.status_code == 409
    assert "make restart" in resp.json()["detail"]
```

`test_dev_apply_writes_sentinel` 若 `dev_status` 被整段 mock，则不要再 mock `head_revision_for_dev`。实现时 API 只调 `dev_status` + `write_reload_sentinel`。

- [ ] **Step 2: 跑测试确认失败**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_system_api.py::test_dev_status_idle -v`

Expected: FAIL 404

- [ ] **Step 3: 把 git 列举挪到 `devplaybook.git_changed_files`**

从 `scripts/dev_status.py` 移入 `opencam/devplaybook.py`（`subprocess` + `Path`）。`dev_status.py` 改为 `from opencam.devplaybook import classify, format_status, git_changed_files`。

- [ ] **Step 4: 实现 API**

`opencam/api/system.py` 增加：

```python
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..devplaybook import (
    ROOT as _DEV_ROOT,  # 若没有 ROOT，用 Path(__file__).resolve().parents[2]
    current_schema_pair,
    dev_status,
    git_changed_files,
    write_reload_sentinel,
)

# 不要 current_schema_pair：在 handler 里
from .. import migrations
from ..db import get_session


class DevStatusOut(BaseModel):
    reload_on: bool
    state: str
    title: str
    detail: str
    steps: list[str]
    can_apply: bool


def _dev_snapshot() -> DevStatusOut:
    session = get_session()
    try:
        bind = session.get_bind()
        schema_rev = migrations.current_revision(bind)
    finally:
        session.close()
    root = Path(__file__).resolve().parents[2]
    st = dev_status(
        reload_on=os.environ.get("OPENCAM_RELOAD", "0") == "1",
        changed_paths=git_changed_files(root),
        schema_rev=schema_rev,
        schema_head=migrations.head_revision(),
    )
    return DevStatusOut(
        reload_on=st.reload_on,
        state=st.state,
        title=st.title,
        detail=st.detail,
        steps=list(st.steps),
        can_apply=st.can_apply,
    )


@router.get("/dev", summary="开发态：热加载与待执行 DDL")
def get_dev_status():
    return _dev_snapshot()


@router.post("/dev/apply", summary="确认后触发一次进程替换以执行迁移")
def apply_dev_ddl():
    snap = _dev_snapshot()
    if os.environ.get("OPENCAM_RELOAD", "0") != "1":
        raise HTTPException(409, "热加载未开启，请在终端执行 make restart")
    if not snap.can_apply:
        raise HTTPException(409, snap.detail or "当前不能执行 DDL，请先 make revision")
    write_reload_sentinel()
    return {"ok": True}
```

`git_changed_files` 签名：

```python
def git_changed_files(repo: Path) -> list[str]:
    ...
```

CORS：在 `opencam/main.py` 创建 `app = FastAPI(...)` 之后：

```python
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

Makefile 的 `ifeq ($(RELOAD),1)` 改为：

```makefile
RELOAD_FLAGS := --reload --reload-dir opencam \
	--reload-exclude models.py \
	--reload-exclude 'migrations/*' \
	--reload-exclude '_dev_reload.py'
```

注意：哨兵必须被 watch 才能触发 reload，**不要** exclude `_dev_reload.py`。纠正为只 exclude `models.py` 与 `migrations/*`：

```makefile
RELOAD_FLAGS := --reload --reload-dir opencam \
	--reload-exclude models.py \
	--reload-exclude 'migrations/*'
```

`.gitignore` 追加：

```
opencam/_dev_reload.py
web/.next/
web/out/
```

保留 `web/dist/` 一行直到 Task 5 删 Vite。

- [ ] **Step 5: 跑 API 测试**

Run: `OPENCAM_DETECTOR=mock uv run pytest tests/test_system_api.py tests/test_devplaybook.py -v`

Expected: PASS

- [ ] **Step 6: 导出 OpenAPI**

Run: `uv run python scripts/export_openapi.py`

Expected: 打印路径数增加 2（`/api/system/dev` GET+POST）

- [ ] **Step 7: Commit**

```bash
git add opencam/api/system.py opencam/main.py opencam/devplaybook.py scripts/dev_status.py Makefile .gitignore tests/test_system_api.py docs/openapi.json
git commit -m "$(cat <<'EOF'
feat: 开发态 DDL 确认 API 与 reload 排除迁移文件

EOF
)"
```

---

### Task 3: `api()` 失败打 `console.error`，撞名路径走 `NEXT_PUBLIC_API_URL`

**Files:**
- Modify: `web/src/lib/api.ts`
- Test: `web/src/lib/api.test.ts`

**Interfaces:**
- Consumes: 现有 `api<T>(path, options)`
- Produces: 非 2xx 在 throw 前 `console.error("[api]", status, path, message)`；`resolveApiUrl(path)` 对 `/cameras` `/events` `/rules` `/training` `/videos` `/models` 及子路径，若 `process.env.NEXT_PUBLIC_API_URL` 非空则加前缀

- [ ] **Step 1: 写失败测试**

`web/src/lib/api.test.ts` 追加：

```ts
it("console.errors on non-2xx", async () => {
  const spy = vi.spyOn(console, "error").mockImplementation(() => {});
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: "boom" }),
    }),
  );
  await expect(api("/api/config")).rejects.toBeInstanceOf(ApiError);
  expect(spy).toHaveBeenCalled();
  const args = spy.mock.calls[0].map(String).join(" ");
  expect(args).toContain("500");
  expect(args).toContain("/api/config");
});

it("prefixes colliding REST paths when NEXT_PUBLIC_API_URL is set", async () => {
  vi.stubEnv("NEXT_PUBLIC_API_URL", "http://127.0.0.1:8600");
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => [],
  });
  vi.stubGlobal("fetch", fetchMock);
  await api("/cameras");
  expect(fetchMock).toHaveBeenCalledWith(
    "http://127.0.0.1:8600/cameras",
    undefined,
  );
});
```

Vitest 4：`vi.stubEnv` 可用；若没有则 `vi.stubGlobal("process", { env: { ...process.env, NEXT_PUBLIC_API_URL: "..." } })` 对 Next 编译期 `process.env.NEXT_PUBLIC_*` 在测试里用：

```ts
import { resolveApiUrl } from "./api";
```

把 `resolveApiUrl` 导出以便单测，`api()` 内部调用它。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/lib/api.test.ts`

Expected: FAIL（没有 console.error / resolveApiUrl）

- [ ] **Step 3: 实现**

```ts
const COLLIDING = [
  "/cameras",
  "/videos",
  "/events",
  "/rules",
  "/training",
  "/models",
];

export function resolveApiUrl(path: string): string {
  const base = (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL) || "";
  if (!base) return path;
  if (COLLIDING.some((p) => path === p || path.startsWith(`${p}/`))) {
    return `${base.replace(/\/$/, "")}${path}`;
  }
  return path;
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(resolveApiUrl(path), options);
  if (!resp.ok) {
    // ... 现有 detail 解析 ...
    const message = formatDetail(detail, `HTTP ${resp.status}`);
    console.error("[api]", `<- ${resp.status} ${path}`, { message });
    throw new ApiError(resp.status, message);
  }
  ...
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd web && npx vitest run src/lib/api.test.ts`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api.ts web/src/lib/api.test.ts
git commit -m "$(cat <<'EOF'
feat: API 失败写入 console.error 供 Next Overlay 收集

EOF
)"
```

---

### Task 4: `web/` 从 Vite 换成 Next.js 16

**Files:**
- Create: `web/next.config.ts`、`web/postcss.config.mjs`、`web/.env.development`、`web/src/app/layout.tsx`、`web/src/app/[[...slug]]/page.tsx`
- Modify: `web/package.json`、`web/tsconfig.json`、`web/tsconfig.app.json`、`web/src/app/providers.tsx`、`web/src/components/app/app-shell.tsx`
- Delete: `web/vite.config.ts`、`web/index.html`、`web/src/main.tsx`、`web/src/app/router.tsx`、`web/tsconfig.node.json`（若只给 Vite 用）
- Create: `web/vitest.config.ts`（vitest 不再挂在 vite.config）
- Modify: `Makefile` `ui` / `ui-build`

**Interfaces:**
- Consumes: 现有 `@/pages/*` 页面组件、`AppShell`、`Providers`
- Produces: `next dev --port 5173`；`next build` 静态导出到 `web/out`

锁定 catch-all：`output: 'export'` 无法枚举 `/cameras/[id]`。`next dev` 仍有 Dev Overlay。

- [ ] **Step 1: 改 package.json 依赖与脚本**

`scripts`:

```json
{
  "dev": "next dev --port 5173",
  "build": "next build",
  "test": "vitest run",
  "lint": "oxlint"
}
```

dependencies 增加 `"next": "^16.2.5"`，去掉 `react-router`、`@tailwindcss/vite`、`vite`、`@vitejs/plugin-react`。devDependencies 增加 `"@tailwindcss/postcss": "^4.3.3"`，保留 vitest / jsdom / testing-library。

然后在 `web/` 执行 `npm install`。

- [ ] **Step 2: Next / PostCSS / env / tsconfig**

`web/next.config.ts`：

```ts
import type { NextConfig } from "next";
import path from "node:path";

const API = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8600";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  turbopack: {
    root: path.resolve(import.meta.dirname),
  },
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${API}/api/:path*` },
      { source: "/health", destination: `${API}/health` },
      { source: "/docs", destination: `${API}/docs` },
      { source: "/redoc", destination: `${API}/redoc` },
      { source: "/openapi.json", destination: `${API}/openapi.json` },
      { source: "/videos/:path*", destination: `${API}/videos/:path*` },
      { source: "/videos", destination: `${API}/videos` },
      { source: "/models/:path*", destination: `${API}/models/:path*` },
      { source: "/models", destination: `${API}/models` },
    ];
  },
};

export default nextConfig;
```

注意：`output: 'export'` 时 **rewrites 在 `next start`/静态导出不生效**，只在 `next dev` 生效。这正是要的。

`web/postcss.config.mjs`：

```js
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};
export default config;
```

`web/.env.development`：

```
NEXT_PUBLIC_API_URL=http://127.0.0.1:8600
```

`web/.env.production`：

```
NEXT_PUBLIC_API_URL=
```

`web/tsconfig.json` 按 Next 自动生成的结构，保留 `"@/*": ["./src/*"]`，加入 `"jsx": "preserve"` 与 `"plugins": [{ "name": "next" }]`。从 `tsconfig.app.json` 去掉 `"types": ["vite/client"]`。

`web/vitest.config.ts`：

```ts
import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  test: { environment: "jsdom", setupFiles: ["./src/test/setup.ts"] },
});
```

- [ ] **Step 3: layout + catch-all + 改 AppShell / Providers**

`web/src/app/layout.tsx`：

```tsx
import type { ReactNode } from "react";
import { Providers } from "@/app/providers";
import { AppShell } from "@/components/app/app-shell";
import "@/index.css";

export const metadata = { title: "open-cam 控制台" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
```

若 `index.css` 在 `src/index.css`，import 保持 `@/index.css`。Geist 字体现有 `@import "@fontsource-variable/geist"` 可继续用。

`web/src/app/providers.tsx` 去掉 `RouterProvider`，只保留 Theme + Query + Toaster + `{children}`。

`web/src/app/[[...slug]]/page.tsx`：

```tsx
"use client";

import { usePathname } from "next/navigation";
import { CameraDetailPage } from "@/pages/camera-detail";
import { CamerasPage } from "@/pages/cameras";
import { DashboardPage } from "@/pages/dashboard";
import { EventsPage } from "@/pages/events";
import { MarketplacePage } from "@/pages/marketplace";
import { RulesPage } from "@/pages/rules";
import { SettingsPage } from "@/pages/settings";
import { TrainingPage } from "@/pages/training";

export default function CatchAllPage() {
  const pathname = usePathname() || "/";
  const p = pathname.replace(/\/$/, "") || "/";
  if (p === "/") return <DashboardPage />;
  if (p === "/cameras") return <CamerasPage />;
  if (p.startsWith("/cameras/")) return <CameraDetailPage />;
  if (p === "/rules") return <RulesPage />;
  if (p === "/events") return <EventsPage />;
  if (p === "/training" || p.startsWith("/training/")) return <TrainingPage />;
  if (p === "/marketplace") return <MarketplacePage />;
  if (p === "/settings") return <SettingsPage />;
  return <DashboardPage />;
}
```

`CameraDetailPage` / `TrainingPage` 里若用 `useParams` from `react-router`，改为 `next/navigation` 的 `useParams()`。全仓库搜 `react-router`，全部换成 `next/link` 或 `next/navigation`。

`app-shell.tsx`：`NavLink` → `Link` from `next/link`；`usePathname()` 判断 `isActive`；`Outlet` 改为 `{children}`。签名改为 `export function AppShell({ children }: { children: React.ReactNode })`。

- [ ] **Step 4: 删除 Vite 入口并跑前端测试**

删除 `web/index.html`、`web/src/main.tsx`、`web/src/app/router.tsx`、`web/vite.config.ts`。

Run: `cd web && npx vitest run`

Expected: PASS（若页面测试仍 mock router，改 mock `next/navigation`）

- [ ] **Step 5: `next build` 确认导出 `web/out/index.html`**

Run: `cd web && npx next build`

Expected: 成功；存在 `web/out/index.html`

若失败（rewrites + export 冲突）：把 `rewrites()` 包在 `process.env.NODE_ENV === "development"` 判断里，或拆 `next.config.ts`：export 时不加 rewrites（dev 仍加）。`output: 'export'` 对 `next dev` 仍可用。

- [ ] **Step 6: Makefile**

`ui-build` 保持 `cd web && npm ci && npm run build`。产物目录改为 `out`（下一步 FastAPI 跟着改）。

- [ ] **Step 7: Commit**

```bash
git add web Makefile
git commit -m "$(cat <<'EOF'
feat: 控制台改用 Next.js 16 以启用 Dev Overlay

EOF
)"
```

---

### Task 5: DDL 横幅（不要仿 N Issues）

**Files:**
- Create: `web/src/components/app/dev-banner.tsx`
- Create: `web/src/components/app/dev-banner.test.tsx`
- Modify: `web/src/components/app/app-shell.tsx`（在 `main` 上方挂横幅）
- Modify: `web/src/lib/system.ts`（DevStatus 类型，若已有 system types 则补上）

**Interfaces:**
- Consumes: `GET /api/system/dev`、`POST /api/system/dev/apply`、`GET /health`
- Produces: `DevBanner` 组件

- [ ] **Step 1: 写失败测试**

```tsx
import { render, screen } from "@testing-library/react";
import { DevBanner } from "./dev-banner";

it("shows confirm on need_apply", () => {
  render(
    <DevBanner
      status={{
        reload_on: true,
        state: "need_apply",
        title: "待执行数据库迁移",
        detail: "确认后将重启",
        steps: [],
        can_apply: true,
      }}
      health="ok"
      onApply={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "确认并重启" })).toBeEnabled();
});

it("disables confirm on need_revision", () => {
  render(
    <DevBanner
      status={{
        reload_on: true,
        state: "need_revision",
        title: "表结构已改，还没有迁移脚本",
        detail: "请先 make revision",
        steps: [],
        can_apply: false,
      }}
      health="ok"
      onApply={() => {}}
    />,
  );
  expect(screen.queryByRole("button", { name: "确认并重启" })).toBeNull();
  expect(screen.getByText(/revision/)).toBeInTheDocument();
});

it("shows loading when health is down", () => {
  render(
    <DevBanner
      status={{
        reload_on: true, state: "idle", title: "", detail: "", steps: [], can_apply: false,
      }}
      health="down"
      onApply={() => {}}
    />,
  );
  expect(screen.getByText("正在加载…")).toBeInTheDocument();
});
```

idle + health ok 时组件返回 `null`（不占地方）。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd web && npx vitest run src/components/app/dev-banner.test.tsx`

Expected: FAIL

- [ ] **Step 3: 实现 `DevBanner` 与壳里轮询**

横幅：`border` + amber/red，中文文案来自 API 的 `title`/`detail`。`need_apply` 才有「确认并重启」，点击 `onApply`。文案包含「摄像头会中断几秒」。

`AppShell` 内：`useQuery` key `["system-dev"]` 每 2s 拉 `/api/system/dev`；另每 2s `fetch(resolveApiUrl("/health"))`，失败则 `health=down`。`onApply` → `api("/api/system/dev/apply", { method: "POST" })`。确认后把本地 `health` 置 `down` 直到 `/health` 恢复。60s 仍 down 则显示「启动失败，请看终端或 make restart」。

`idle` 且 health ok 不渲染横幅。Next Overlay 负责前端 Issues。

- [ ] **Step 4: 跑测试**

Run: `cd web && npx vitest run src/components/app/dev-banner.test.tsx`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add web/src/components/app/dev-banner.tsx web/src/components/app/dev-banner.test.tsx web/src/components/app/app-shell.tsx web/src/lib/system.ts
git commit -m "$(cat <<'EOF'
feat: 控制台 DDL 确认横幅

EOF
)"
```

---

### Task 6: FastAPI 挂 `web/out`、文档、`make test`

**Files:**
- Modify: `opencam/main.py`（`DIST = ... / "web" / "out"`；Next export 的资源常在 `_next/static`，SPA fallback 仍回 `out/index.html`；mount `/_next` 若目录存在）
- Modify: `tests/test_web.py`（`web/out/index.html`；断言不再依赖 Vite 的 `/assets/` 也可，改为存在 `id` 或 `open-cam` 文案）
- Modify: `opencam/devplaybook.py` `dist_is_stale` 看 `out/index.html`
- Modify: `.gitignore` 加 `web/out/`，可删对 `web/dist/` 的依赖或两行都留
- Modify: `AGENTS.md`、`README.md`、`Makefile` help：浏览器 5173 = `make start` + `make ui`；Issues 胶囊是 Next 开发工具；DDL 走横幅

- [ ] **Step 1: 改 DIST 与 test_web**

`tests/test_web.py`：

```python
DIST_INDEX = Path(__file__).resolve().parents[1] / "web" / "out" / "index.html"
```

`test_console_index`：断言 200 HTML，含 `open-cam`；不要断言 `/assets/`（Next 用 `/_next/static`）。

- [ ] **Step 2: `make ui-build` 后跑 `test_web`**

Run: `cd web && npm run build`  
再：`OPENCAM_DETECTOR=mock uv run pytest tests/test_web.py -v`

Expected: PASS

- [ ] **Step 3: 全量测试**

Run: `make test`  
以及 `cd web && npm test`

Expected: 全绿

- [ ] **Step 4: 更新 AGENTS.md / README / Makefile help**

写明：本地改控制台用 5173；左下角 N 不是产品功能；改表结构横幅确认，不是点 N。

- [ ] **Step 5: Commit**

```bash
git add opencam/main.py opencam/devplaybook.py tests/test_web.py AGENTS.md README.md Makefile .gitignore
git commit -m "$(cat <<'EOF'
feat: FastAPI 挂 Next 静态导出并更新开发说明

EOF
)"
```

---

## Spec coverage

| 规格项 | 任务 |
|---|---|
| Next.js 16 + Dev Overlay | Task 4 |
| `console.error` 进 Overlay | Task 3 |
| 两进程 5173/8600、rewrites | Task 4 |
| `--reload-exclude` models/migrations | Task 2 |
| `GET/POST /api/system/dev` | Task 2 |
| 缺脚本禁止 apply | Task 1–2 |
| 横幅确认重启 | Task 5 |
| 静态导出单端口、无 Overlay | Task 4 + 6 |
| CORS 5173 | Task 2 |
| 文档 | Task 6 |
| openapi | Task 2 |

## Self-review

- 无 TBD。哨兵文件**不** exclude。`output: 'export'` 用 catch-all，避免动态 `[id]` 无法枚举。
- `DevStatus.state` / `can_apply` 在 Task 1/2/5 名称一致。
- 前端 Vitest 与 Next 并存靠独立 `vitest.config.ts`。
