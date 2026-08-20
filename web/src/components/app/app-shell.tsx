"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  BookOpen,
  Camera,
  ClipboardCheck,
  LayoutDashboard,
  Monitor,
  Moon,
  Settings,
  ShieldCheck,
  Sun,
  Store,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useEffect, useState, type ReactNode } from "react";
import { DevBanner } from "@/components/app/dev-banner";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api, resolveApiUrl } from "@/lib/api";
import type { DevStatus } from "@/lib/system";
import { themeForSsr } from "@/lib/theme";
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
      { to: "/training", label: "模型训练", icon: ClipboardCheck },
      { to: "/marketplace", label: "方案市场", icon: Store },
    ],
  },
  {
    label: "系统",
    items: [{ to: "/settings", label: "设置", icon: Settings }],
  },
] as const;

const THEMES = [
  { value: "light", label: "浅色", icon: Sun },
  { value: "dark", label: "深色", icon: Moon },
  { value: "system", label: "跟随系统", icon: Monitor },
] as const;

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const resolved = themeForSsr(theme, mounted);
  const current = THEMES.find((item) => item.value === resolved) ?? THEMES[2];
  const Icon = current.icon;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger render={<Button variant="outline" size="sm" />}>
        <Icon />
        主题
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {THEMES.map((item) => (
          <DropdownMenuItem key={item.value} onClick={() => setTheme(item.value)}>
            <item.icon />
            {item.label}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/";
  const current = pathname.replace(/\/$/, "") || "/";
  const [health, setHealth] = useState<"ok" | "down">("ok");
  const [applyAt, setApplyAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

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
      <aside className="flex h-svh min-h-0 w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
        <div className="px-4 py-4 text-base font-medium">open-cam</div>
        <nav aria-label="主导航" className="min-h-0 flex flex-1 flex-col gap-5 overflow-y-auto px-2">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="space-y-1">
              <p className="px-2 text-[11px] font-medium tracking-wide text-muted-foreground">
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
                    className={cn(
                      "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
                      active
                        ? "bg-sidebar-accent text-sidebar-accent-foreground"
                        : "text-sidebar-foreground/80 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground",
                    )}
                  >
                    <Icon aria-hidden="true" className="size-4" />
                    {item.label}
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
              className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-sidebar-foreground/80 hover:bg-sidebar-accent/70 hover:text-sidebar-foreground"
            >
              <BookOpen aria-hidden="true" className="size-4" />
              API 文档
              <span aria-hidden="true" className="ml-auto text-xs">↗</span>
            </a>
          </div>
        </nav>
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end border-b px-4 py-2">
          <ThemeToggle />
        </header>
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
