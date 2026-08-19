import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PageHeader } from '@/components/app/page-header'
import { api, jsonInit } from '@/lib/api'
import { RULE_TYPE_NAMES } from '@/lib/labels'
import type { Camera } from '@/lib/types'

type Pack = {
  id: string
  name: string
  origin: string
  vertical: string
  version: string
  author?: string
  description: string
  rules: Array<{ name: string; type?: string }>
}

export function MarketplacePage() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [packs, setPacks] = useState<Pack[]>([])
  const [note, setNote] = useState('')
  const [source, setSource] = useState('')
  const [camFor, setCamFor] = useState<Record<string, string>>({})

  async function reload() {
    const list = await api<Pack[]>('/api/packs')
    setPacks(list)
  }

  useEffect(() => {
    void (async () => {
      try {
        const cams = await api<Camera[]>('/cameras')
        setCameras(cams)
        await reload()
        const online = await api<{ note?: string }>('/api/packs/online').catch(() => null)
        if (online?.note) setNote(online.note)
      } catch (err) {
        toast.error((err as Error).message)
      }
    })()
  }, [])

  return (
    <div>
      <PageHeader title="方案市场" />
      <div className="mb-4 flex flex-wrap gap-2 rounded-xl border p-4">
        <Input
          className="min-w-64 flex-1"
          placeholder="本地目录 / pack.zip 路径 / https://... 包地址"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
        <Button
          onClick={async () => {
            if (!source.trim()) { toast.error('请填写安装源'); return }
            try {
              const pack = await api<Pack>('/api/packs/install', jsonInit('POST', { source: source.trim() }))
              toast.success(`已安装：${pack.name}`)
              await reload()
            } catch (err) { toast.error((err as Error).message) }
          }}
        >
          安装
        </Button>
      </div>
      {note ? <p className="mb-4 text-sm text-muted-foreground">{note}</p> : null}
      <div className="grid gap-4 sm:grid-cols-2">
        {packs.map((p) => (
          <div key={p.id} className="rounded-xl border p-4">
            <h3 className="font-medium">
              {p.name} <span className="text-xs text-muted-foreground">{p.origin === 'builtin' ? '内置' : '已安装'}</span>
            </h3>
            <div className="text-sm text-muted-foreground">{p.vertical} · v{p.version} · {p.author || '匿名'}</div>
            <p className="mt-2 text-sm">{p.description}</p>
            <div className="mt-2 text-xs text-muted-foreground">规则模板：{p.rules.map((r) => r.name).join('、')}</div>
            <div className="mt-3 flex flex-wrap gap-2">
              <select
                className="h-8 rounded-lg border px-2 text-sm"
                value={camFor[p.id] || (cameras[0] ? String(cameras[0].id) : '')}
                onChange={(e) => setCamFor((m) => ({ ...m, [p.id]: e.target.value }))}
              >
                {cameras.map((c) => (
                  <option key={c.id} value={c.id}>{`应用到：[${c.id}] ${c.name}`}</option>
                ))}
              </select>
              <Button
                disabled={!cameras.length}
                onClick={async () => {
                  try {
                    const camId = Number(camFor[p.id] || cameras[0]?.id)
                    const rules = await api<Array<{ type: string }>>(`/api/packs/${p.id}/apply`, jsonInit('POST', { camera_id: camId }))
                    toast.success(`已应用 ${rules.length} 条规则（含：${rules.map((r) => RULE_TYPE_NAMES[r.type] || r.type).join('、')}），可到「规则」页调整`)
                  } catch (err) { toast.error((err as Error).message) }
                }}
              >
                应用
              </Button>
              {p.origin === 'installed' ? (
                <Button variant="destructive" onClick={async () => {
                  try {
                    await api(`/api/packs/${p.id}`, { method: 'DELETE' })
                    toast.success('已卸载')
                    await reload()
                  } catch (err) { toast.error((err as Error).message) }
                }}>卸载</Button>
              ) : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
