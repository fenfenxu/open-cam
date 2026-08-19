import { NavLink, Outlet, useLocation } from 'react-router'
import { Monitor, Moon, Sun } from 'lucide-react'
import { useTheme } from 'next-themes'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

const NAV = [
  { to: '/', label: '仪表盘', end: true },
  { to: '/cameras', label: '摄像头' },
  { to: '/rules', label: '规则' },
  { to: '/events', label: '事件' },
  { to: '/training', label: '模型训练' },
  { to: '/marketplace', label: '方案市场' },
  { to: '/settings', label: '设置' },
]

const CRUMBS: Record<string, string> = {
  '/': '仪表盘',
  '/cameras': '摄像头',
  '/rules': '规则',
  '/events': '事件',
  '/training': '模型训练',
  '/marketplace': '方案市场',
  '/settings': '设置',
}

function crumb(pathname: string): string {
  if (CRUMBS[pathname]) return CRUMBS[pathname]
  if (pathname.startsWith('/cameras/')) return '摄像头详情'
  if (pathname.startsWith('/training/')) return '模型训练'
  return 'open-cam'
}

export function AppShell() {
  const { setTheme } = useTheme()
  const location = useLocation()
  return (
    <div className="flex min-h-svh bg-background text-foreground">
      <aside className="flex w-56 shrink-0 flex-col border-r bg-sidebar text-sidebar-foreground">
        <div className="px-4 py-4 text-lg font-semibold tracking-tight">open-cam</div>
        <nav className="flex flex-1 flex-col gap-0.5 px-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  'rounded-md px-3 py-2 text-sm transition-colors hover:bg-sidebar-accent',
                  isActive && 'bg-sidebar-accent font-medium text-sidebar-accent-foreground',
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
            className="rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-sidebar-accent"
          >
            API 文档 ↗
          </a>
        </nav>
        <div className="border-t px-4 py-3 text-xs text-muted-foreground">
          本地运行 · 数据不出本机
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-12 items-center justify-between border-b px-4">
          <div className="text-sm text-muted-foreground">{crumb(location.pathname)}</div>
          <DropdownMenu>
            <DropdownMenuTrigger
              render={<Button variant="outline" size="sm" />}
            >
              主题
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => setTheme('light')}>
                <Sun /> 浅色
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setTheme('dark')}>
                <Moon /> 深色
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setTheme('system')}>
                <Monitor /> 跟随系统
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </header>
        <main className="min-h-0 flex-1 overflow-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
