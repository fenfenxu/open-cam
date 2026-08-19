import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PageHeader } from '@/components/app/page-header'
import { api, jsonInit } from '@/lib/api'
import { RULE_TYPE_NAMES } from '@/lib/labels'
import type { Camera } from '@/lib/types'

type SysInfo = {
  version: string
  device: string
  device_config: string
  memory_total_gb?: number | null
  vram_total_gb?: number | null
  detector: string
  yolo_model: string
  detect_fps: number
  packs_available: number
  packs_installed: number
  vlm_configured: boolean
  vlm_model?: string
}

type Acct = {
  platform_base_url?: string
  logged_in: boolean
  note?: string
}

type Channel = {
  id: number
  name: string
  webhook: string
  camera_id?: number | null
  rule_type?: string | null
  enabled: boolean
}

export function SettingsPage() {
  const qc = useQueryClient()
  const sys = useQuery({ queryKey: ['sys'], queryFn: () => api<SysInfo>('/api/system/info') })
  const acct = useQuery({ queryKey: ['acct'], queryFn: () => api<Acct>('/api/account/status') })
  const channels = useQuery({ queryKey: ['notify'], queryFn: () => api<Channel[]>('/api/notify-channels') })
  const cameras = useQuery({ queryKey: ['cameras'], queryFn: () => api<Camera[]>('/cameras') })
  return (
    <div>
      <PageHeader title="设置" />
      <div className="mb-4 rounded-xl border p-4">
        <h3 className="mb-3 font-medium">系统信息</h3>
        {sys.isError ? (
          <p className="text-muted-foreground">系统信息获取失败：{(sys.error as Error).message}</p>
        ) : sys.data ? (
          <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
            <dt className="text-muted-foreground">版本</dt><dd>{sys.data.version}</dd>
            <dt className="text-muted-foreground">推理设备</dt><dd>{sys.data.device}（配置：{sys.data.device_config}）</dd>
            <dt className="text-muted-foreground">系统内存</dt><dd>{sys.data.memory_total_gb ?? '未知'} GB</dd>
            <dt className="text-muted-foreground">显存</dt><dd>{sys.data.vram_total_gb ?? '—'} {sys.data.vram_total_gb ? 'GB' : ''}</dd>
            <dt className="text-muted-foreground">检测器</dt><dd>{sys.data.detector}（{sys.data.yolo_model}）</dd>
            <dt className="text-muted-foreground">采样帧率</dt><dd>{sys.data.detect_fps} fps</dd>
            <dt className="text-muted-foreground">方案包</dt><dd>可用 {sys.data.packs_available} 个，其中已安装 {sys.data.packs_installed} 个</dd>
            <dt className="text-muted-foreground">VLM 复核</dt>
            <dd>
              {sys.data.vlm_configured
                ? `已配置（${sys.data.vlm_model}）`
                : '未配置 OPENCAM_VLM_API_KEY，事件将标记为 skipped'}
            </dd>
          </dl>
        ) : (
          <p className="text-muted-foreground">加载中…</p>
        )}
      </div>
      <div className="rounded-xl border p-4">
        <h3 className="mb-3 font-medium">平台账号</h3>
        {acct.isError ? (
          <p className="text-muted-foreground">账号状态获取失败：{(acct.error as Error).message}</p>
        ) : acct.data ? (
          <>
            <dl className="grid grid-cols-[8rem_1fr] gap-y-1 text-sm">
              <dt className="text-muted-foreground">平台</dt><dd>{acct.data.platform_base_url || '未配置'}</dd>
              <dt className="text-muted-foreground">登录状态</dt><dd>{acct.data.logged_in ? '已登录' : '未登录'}</dd>
            </dl>
            {acct.data.note ? <p className="mt-2 text-sm text-muted-foreground">{acct.data.note}</p> : null}
          </>
        ) : (
          <p className="text-muted-foreground">加载中…</p>
        )}
      </div>
      <NotifyPanel channels={channels.data || []} cameras={cameras.data || []} error={channels.error as Error | null} onReload={() => void qc.invalidateQueries({ queryKey: ['notify'] })} />
    </div>
  )
}

function NotifyPanel({
  channels, cameras, error, onReload,
}: {
  channels: Channel[]
  cameras: Camera[]
  error: Error | null
  onReload: () => void
}) {
  const camName = (id: number | null | undefined) => {
    if (id == null) return '全部摄像头'
    const c = cameras.find((x) => x.id === id)
    return c ? `[${c.id}] ${c.name}` : `#${id}`
  }
  return (
    <div className="mt-4 rounded-xl border p-4">
      <h3 className="mb-2 font-medium">通知渠道</h3>
      <p className="mb-3 text-sm text-muted-foreground">事件命中后自动推送到 webhook（兼容飞书 / 企业微信 / 钉钉机器人）；适用范围留空表示全部。</p>
      {error ? <p className="text-muted-foreground">通知渠道获取失败：{error.message}</p> : null}
      {channels.length === 0 && !error ? <p className="text-sm text-muted-foreground">还没有通知渠道。</p> : (
        <ul className="mb-3 space-y-2 text-sm">
          {channels.map((ch) => (
            <li key={ch.id} className="flex flex-wrap items-center gap-2 border-b pb-2">
              <span>{ch.name}</span>
              <span className="max-w-48 truncate font-mono text-xs">{ch.webhook}</span>
              <span className="text-muted-foreground">{camName(ch.camera_id)} · {RULE_TYPE_NAMES[ch.rule_type || ''] || '全部类型'}</span>
              <Button size="sm" variant="outline" onClick={async () => {
                const r = await api<{ ok: boolean; error?: string }>(`/api/notify-channels/${ch.id}/test`, { method: 'POST' })
                toast[r.ok ? 'success' : 'error'](r.ok ? '测试推送成功' : `推送失败：${r.error}`)
              }}>测试</Button>
              <Button size="sm" variant="destructive" onClick={async () => {
                await api(`/api/notify-channels/${ch.id}`, { method: 'DELETE' })
                toast.success('已删除')
                onReload()
              }}>删除</Button>
            </li>
          ))}
        </ul>
      )}
      <NotifyAdd cameras={cameras} onReload={onReload} />
    </div>
  )
}

function NotifyAdd({ cameras, onReload }: { cameras: Camera[]; onReload: () => void }) {
  return (
    <form
      className="flex flex-wrap gap-2"
      onSubmit={async (e) => {
        e.preventDefault()
        const fd = new FormData(e.currentTarget)
        const name = String(fd.get('name') || '').trim()
        const webhook = String(fd.get('webhook') || '').trim()
        if (!name || !webhook) { toast.error('请填写名称和 webhook URL'); return }
        try {
          await api('/api/notify-channels', jsonInit('POST', {
            name,
            webhook,
            camera_id: fd.get('camera_id') || null,
            rule_type: fd.get('rule_type') || null,
          }))
          toast.success('已添加')
          ;(e.target as HTMLFormElement).reset()
          onReload()
        } catch (err) { toast.error((err as Error).message) }
      }}
    >
      <Input name="name" placeholder="联系人/渠道名" className="w-36" />
      <Input name="webhook" placeholder="webhook URL" className="min-w-52 flex-1" />
      <select name="camera_id" className="h-8 rounded-lg border px-2 text-sm">
        <option value="">全部摄像头</option>
        {cameras.map((c) => <option key={c.id} value={c.id}>{`[${c.id}] ${c.name}`}</option>)}
      </select>
      <select name="rule_type" className="h-8 rounded-lg border px-2 text-sm">
        <option value="">全部类型</option>
        {Object.entries(RULE_TYPE_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <Button type="submit">添加</Button>
    </form>
  )
}
