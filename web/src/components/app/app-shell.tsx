import { NavLink, Outlet } from "react-router";
import { Monitor, Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "仪表盘", end: true },
  { to: "/cameras", label: "摄像头" },
  { to: "/rules", label: "规则" },
  { to: "/events", label: "事件" },
  { to: "/training", label: "模型训练" },
  { to: "/marketplace", label: "方案市场" },
  { to: "/settings", label: "设置" },
] as const;

const THEMES = [
  { value: "light", label: "浅色", icon: Sun },
  { value: "dark", label: "深色", icon: Moon },
  { value: "system", label: "跟随系统", icon: Monitor },
] as const;

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const current = THEMES.find((item) => item.value === theme) ?? THEMES[2];
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

export function AppShell() {
  return (
    <div className="flex min-h-svh bg-background text-foreground">
      <aside className="flex w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
        <div className="px-4 py-4 text-base font-medium">open-cam</div>
        <nav className="flex flex-1 flex-col gap-0.5 px-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={"end" in item ? item.end : false}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-2 py-1.5 text-sm",
                  isActive
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "hover:bg-sidebar-accent/70",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
          <a
            href="/docs"
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-md px-2 py-1.5 text-sm hover:bg-sidebar-accent/70"
          >
            API 文档 ↗
          </a>
        </nav>
        <div className="border-t px-4 py-3 text-xs text-muted-foreground">
          本地运行 · 数据不出本机
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-end border-b px-4 py-2">
          <ThemeToggle />
        </header>
        <main className="min-w-0 flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
