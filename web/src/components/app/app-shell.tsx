"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BrainCircuit,
  BookOpen,
  Camera,
  ClipboardCheck,
  LayoutDashboard,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  ShieldCheck,
  Store,
} from "lucide-react";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { DevBanner } from "@/components/app/dev-banner";
import { api, resolveApiUrl } from "@/lib/api";
import type { DevStatus } from "@/lib/system";
import { cn } from "@/lib/utils";

const NAV_GROUPS = [
  {
    label: "工作台",
    items: [
      { to: "/", label: "仪表盘", end: true, icon: LayoutDashboard },
      { to: "/cameras", label: "摄像头", icon: Camera },
      { to: "/events", label: "事件", icon: Activity },
    ],
  },
  {
    label: "策略与模型",
    items: [
      { to: "/rules", label: "规则", icon: ShieldCheck },
      { to: "/models", label: "模型管理", icon: BrainCircuit },
      { to: "/training", label: "模型训练", icon: ClipboardCheck },
      { to: "/marketplace", label: "方案市场", icon: Store },
    ],
  },
  {
    label: "系统",
    items: [{ to: "/settings", label: "设置", icon: Settings }],
  },
] as const;

const SIDEBAR_STORAGE_KEY = "opencam-sidebar-collapsed";
let sidebarMemoryPreference = false;

function subscribeSidebarPreference(onChange: () => void) {
  if (typeof window === "undefined") return () => {};
  window.addEventListener("storage", onChange);
  window.addEventListener("opencam-sidebar-change", onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener("opencam-sidebar-change", onChange);
  };
}

function getSidebarPreference() {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "1";
  } catch {
    return sidebarMemoryPreference;
  }
}

function setSidebarPreference(collapsed: boolean) {
  sidebarMemoryPreference = collapsed;
  try {
    window.localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // 无痕模式或浏览器禁用存储时仍允许当前页面折叠。
  }
  window.dispatchEvent(new Event("opencam-sidebar-change"));
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const current = pathname.replace(/\/$/, "") || "/";
  const [health, setHealth] = useState<"ok" | "down">("ok");
  const [applyAt, setApplyAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const sidebarCollapsed = useSyncExternalStore(
    subscribeSidebarPreference,
    getSidebarPreference,
    () => false,
  );

  const devQuery = useQuery({
    queryKey: ["system-dev"],
    queryFn: () => api<DevStatus>("/api/system/dev"),
    refetchInterval: 2000,
  });

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const resp = await fetch(resolveApiUrl("/health"));
        if (!cancelled) setHealth(resp.ok ? "ok" : "down");
        if (resp.ok && !cancelled) setApplyAt(null);
      } catch {
        if (!cancelled) setHealth("down");
      }
    }
    tick();
    const id = window.setInterval(tick, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (health !== "down" || applyAt == null) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [health, applyAt]);

  const apply = useMutation({
    mutationFn: () => api("/api/system/dev/apply", { method: "POST" }),
    onSuccess: () => {
      setHealth("down");
      setApplyAt(Date.now());
    },
  });

  const idle: DevStatus = {
    reload_on: true,
    state: "idle",
    title: "",
    detail: "",
    steps: [],
    can_apply: false,
  };
  const stalled = health === "down" && applyAt != null && now - applyAt >= 60_000;

  return (
    <div className="flex h-svh overflow-hidden bg-background text-foreground">
      <aside
        className={cn(
          "flex h-svh min-h-0 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground transition-[width] duration-200 ease-out",
          sidebarCollapsed ? "w-16" : "w-56",
        )}
      >
        <div
          className={cn(
            "flex items-center justify-between py-3",
            sidebarCollapsed ? "px-2" : "px-4",
          )}
        >
          <span
            className={cn("text-base font-medium", sidebarCollapsed && "text-sm")}
            aria-label="open-cam"
            title={sidebarCollapsed ? "open-cam" : undefined}
          >
            {sidebarCollapsed ? "oc" : "open-cam"}
          </span>
          <button
            type="button"
            aria-label={sidebarCollapsed ? "展开菜单" : "收起菜单"}
            aria-expanded={!sidebarCollapsed}
            title={sidebarCollapsed ? "展开菜单" : "收起菜单"}
            className="flex size-8 items-center justify-center rounded-md text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-foreground focus-visible:ring-2 focus-visible:ring-sidebar-ring"
            onClick={() => setSidebarPreference(!sidebarCollapsed)}
          >
            {sidebarCollapsed ? (
              <PanelLeftOpen aria-hidden="true" className="size-4" />
            ) : (
              <PanelLeftClose aria-hidden="true" className="size-4" />
            )}
          </button>
        </div>
        <nav
          aria-label="主导航"
          className="min-h-0 flex flex-1 flex-col gap-5 overflow-y-auto px-2"
        >
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="space-y-1">
              <p
                className={cn(
                  "px-2 text-[11px] font-medium tracking-wide text-muted-foreground",
                  sidebarCollapsed && "sr-only",
                )}
              >
                {group.label}
              </p>
              {group.items.map((item) => {
                const active = "end" in item && item.end
                  ? current === item.to
                  : current === item.to || current.startsWith(`${item.to}/`);
                const Icon = item.icon;
                return (
                  <Link
                    key={item.to}
                    href={item.to}
                    prefetch={false}
                    aria-current={active ? "page" : undefined}
                    title={sidebarCollapsed ? item.label : undefined}
                    className={cn(
                      "flex items-center gap-2 rounded-md py-1.5 text-sm",
                      sidebarCollapsed ? "justify-center px-2" : "px-2",
                      active
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-sidebar-foreground/80 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground",
                    )}
                  >
                    <Icon aria-hidden="true" className="size-4" />
                    <span className={sidebarCollapsed ? "sr-only" : undefined}>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          ))}
          <div className="mt-auto border-t pt-3">
            <a
              href="/docs"
              target="_blank"
              rel="noopener noreferrer"
              title={sidebarCollapsed ? "API 文档" : undefined}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-sidebar-foreground/80 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground"
            >
              <BookOpen aria-hidden="true" className="size-4" />
              <span className={sidebarCollapsed ? "sr-only" : undefined}>API 文档</span>
              <span aria-hidden="true" className={cn("ml-auto text-xs", sidebarCollapsed && "hidden")}>↗</span>
            </a>
          </div>
        </nav>
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <DevBanner
          status={devQuery.data ?? idle}
          health={health}
          stalled={stalled}
          applying={applyAt != null}
          onApply={() => apply.mutate()}
        />
        <main className="min-h-0 min-w-0 flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  );
}
