import { useMemo, useState } from 'react'
import { Link } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { DataTable, type Column } from '@/components/app/data-table'
import { DetailDrawer } from '@/components/app/detail-drawer'
import { PageHeader } from '@/components/app/page-header'
import { api, jsonInit, fmtTime } from '@/lib/api'
import {
  ACTION_NAMES,
  eventStatus,
  fmtClipRange,
  fmtMediaTime,
  NEXT_ACTIONS,
  RULE_TYPE_NAMES,
  STATUS_NAMES,
} from '@/lib/labels'
import type { Camera, EventItem } from '@/lib/types'

type ActionRow = { ts: number; action: string; actor: string; payload?: Record<string, unknown> }

function cameraLabel(e: EventItem) {
  return e.camera_name || `#${e.camera_id}`
}

function sourceLabel(e: EventItem) {
  if (e.source_filename) return e.source_filename
  return e.source_offset == null ? '直播流（无回放）' : '—'
}

function fmtPayload(a: ActionRow) {
  const p = a.payload || {}
  if (a.action === 'notify') return p.ok ? '推送成功' : `失败：${p.error || ''}`
  if (a.action === 'status') {
    return `${STATUS_NAMES[String(p.from)] || p.from} → ${STATUS_NAMES[String(p.to)] || p.to}`
  }
  if (a.action === 'assign') return `→ ${p.to || '（取消指派）'}`
  if (a.action === 'note') return String(p.text || '')
  return ''
}

export function EventsPage() {
  const qc = useQueryClient()
  const [cameraId, setCameraId] = useState('')
  const [ruleType, setRuleType] = useState('')
  const [status, setStatus] = useState('')
  const [verdict, setVerdict] = useState('')
  const [starred, setStarred] = useState(false)
  const [openId, setOpenId] = useState<number | null>(null)
  const [assignee, setAssignee] = useState('')
  const [note, setNote] = useState('')
  const [taskId, setTaskId] = useState('')

  const cameras = useQuery({ queryKey: ['cameras'], queryFn: () => api<Camera[]>('/cameras') })
  const listPath = useMemo(() => {
    const params = new URLSearchParams()
    if (cameraId) params.set('camera_id', cameraId)
    if (ruleType) params.set('rule_type', ruleType)
    if (status) params.set('status', status)
    if (verdict) params.set('vlm_verdict', verdict)
    if (starred) params.set('starred', 'true')
    params.set('limit', '100')
    return `/events?${params}`
  }, [cameraId, ruleType, status, starred, verdict])

  const events = useQuery({ queryKey: ['events', listPath], queryFn: () => api<EventItem[]>(listPath) })
  const detail = useQuery({
    queryKey: ['event', openId],
    queryFn: () => api<EventItem>(`/events/${openId}`),
    enabled: openId != null,
  })
  const actionRows = useQuery({
    queryKey: ['event-actions', openId],
    queryFn: () => api<ActionRow[]>(`/events/${openId}/actions`),
    enabled: openId != null,
  })
  const tasks = useQuery({
    queryKey: ['training-tasks'],
    queryFn: () => api<Array<{ task_id: string; object?: string; property?: string; status: string }>>('/training/tasks'),
    enabled: openId != null,
  })

  const patch = useMutation({
    mutationFn: ({ id, body }: { id: number; body: Record<string, unknown> }) =>
      api(`/events/${id}`, jsonInit('PATCH', body)),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['events'] })
      void qc.invalidateQueries({ queryKey: ['event'] })
      void qc.invalidateQueries({ queryKey: ['event-actions'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const columns = useMemo<Column<EventItem>[]>(() => [
    {
      header: '',
      cell: ({ row }) => (
        <Button size="xs" variant="ghost" onClick={(e) => {
          e.stopPropagation()
          patch.mutate({ id: row.original.id, body: { starred: !row.original.starred } })
        }}>
          {row.original.starred ? '★' : '☆'}
        </Button>
      ),
    },
    { header: '时间', cell: ({ row }) => <span className="font-mono text-xs">{fmtTime(row.original.ts)}</span> },
    {
      header: '摄像头',
      cell: ({ row }) => (
        <div>
          {cameraLabel(row.original)}
          <div className="text-xs text-muted-foreground">{sourceLabel(row.original)}</div>
        </div>
      ),
    },
    { header: '素材', cell: ({ row }) => <span className="font-mono text-xs">{fmtClipRange(row.original)}</span> },
    { header: '类型', cell: ({ row }) => RULE_TYPE_NAMES[row.original.type] || row.original.type },
    { header: '置信度', cell: ({ row }) => row.original.confidence?.toFixed?.(2) },
    {
      header: 'VLM 判定',
      cell: ({ row }) => <Badge variant="secondary">{row.original.vlm_verdict || row.original.vlm_status}</Badge>,
    },
    { header: '处置状态', cell: ({ row }) => STATUS_NAMES[eventStatus(row.original)] || row.original.status },
    { header: '负责人', cell: ({ row }) => row.original.assignee || '—' },
  ], [patch])

  const e = detail.data
  const next = NEXT_ACTIONS[e ? eventStatus(e) : 'open'] ?? []
  const confirmed = (tasks.data || []).filter((t) => t.status === 'confirmed')
  const hasClip = e?.source_offset != null

  return (
    <div>
      <PageHeader title="事件处置" />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className="h-8 rounded-lg border bg-background px-2 text-sm" value={cameraId} onChange={(ev) => setCameraId(ev.target.value)}>
          <option value="">全部摄像头</option>
          {(cameras.data || []).map((c) => <option key={c.id} value={c.id}>{`[${c.id}] ${c.name}`}</option>)}
        </select>
        <select className="h-8 rounded-lg border bg-background px-2 text-sm" value={ruleType} onChange={(ev) => setRuleType(ev.target.value)}>
          <option value="">全部类型</option>
          {Object.entries(RULE_TYPE_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className="h-8 rounded-lg border bg-background px-2 text-sm" value={status} onChange={(ev) => setStatus(ev.target.value)}>
          <option value="">全部状态</option>
          {Object.entries(STATUS_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <select className="h-8 rounded-lg border bg-background px-2 text-sm" value={verdict} onChange={(ev) => setVerdict(ev.target.value)}>
          <option value="">全部判定</option>
          <option value="confirmed">已确认</option>
          <option value="false_alarm">误报</option>
          <option value="uncertain">不确定</option>
        </select>
        <label className="flex items-center gap-1 text-sm">
          <input type="checkbox" checked={starred} onChange={(ev) => setStarred(ev.target.checked)} />
          仅看关注
        </label>
        <Button variant="outline" onClick={() => void events.refetch()}>刷新</Button>
      </div>
      {events.error ? (
        <p className="text-sm text-destructive">{(events.error as Error).message}</p>
      ) : (
        <DataTable
          columns={columns}
          data={events.data || []}
          emptyText="暂无事件"
          onRowClick={(row) => {
            setOpenId(row.id)
            setAssignee(row.assignee || '')
            setNote(row.note || '')
          }}
        />
      )}
      <DetailDrawer open={openId != null} onOpenChange={(o) => { if (!o) setOpenId(null) }} title={e ? `事件 #${e.id}` : '事件'}>
        {!e ? <p className="text-sm text-muted-foreground">加载中…</p> : (
          <div className="space-y-4">
            {e.snapshot_path ? <img src={`/events/${e.id}/snapshot`} alt="快照" className="w-full rounded-md border" /> : null}
            <p className="text-xs text-muted-foreground">
              素材 {fmtClipRange(e)}{e.source_offset == null ? '' : `（命中 ${fmtMediaTime(e.source_offset)}）`}
            </p>
            {hasClip ? (
              <video className="event-clip w-full" controls playsInline src={`/events/${e.id}/clip`} />
            ) : (
              <p className="text-sm text-muted-foreground">该事件没有可回放的视频片段（实时流或升级前的旧数据只有快照）。</p>
            )}
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
              <dt className="text-muted-foreground">摄像头</dt>
              <dd><Link className="underline" to={`/cameras/${e.camera_id}`}>{cameraLabel(e)}</Link></dd>
              <dt className="text-muted-foreground">视频</dt><dd>{sourceLabel(e)}</dd>
              <dt className="text-muted-foreground">素材时段</dt><dd>{fmtClipRange(e)}</dd>
              <dt className="text-muted-foreground">时间</dt><dd>{fmtTime(e.ts)}</dd>
              <dt className="text-muted-foreground">类型</dt><dd>{RULE_TYPE_NAMES[e.type] || e.type}</dd>
              <dt className="text-muted-foreground">置信度</dt><dd>{e.confidence}</dd>
              <dt className="text-muted-foreground">详情</dt><dd className="break-all font-mono text-xs">{JSON.stringify(e.detail)}</dd>
              <dt className="text-muted-foreground">VLM 状态</dt><dd>{e.vlm_status}</dd>
              <dt className="text-muted-foreground">VLM 判定</dt><dd>{e.vlm_verdict || '—'}</dd>
              <dt className="text-muted-foreground">VLM 理由</dt><dd>{e.vlm_reason || '—'}</dd>
            </dl>
            <div className="flex flex-wrap gap-2">
              {next.map(([code, label]) => (
                <Button key={code} size="sm" onClick={() => { patch.mutate({ id: e.id, body: { status: code } }); toast.success('状态已更新') }}>{label}</Button>
              ))}
              <Button size="sm" variant="outline" onClick={async () => {
                try {
                  await api(`/events/${e.id}/notify`, { method: 'POST' })
                  toast.success('已提交重发，稍后查看处置时间线')
                  setTimeout(() => void qc.invalidateQueries({ queryKey: ['event-actions'] }), 1500)
                } catch (err) { toast.error((err as Error).message) }
              }}>重发通知</Button>
            </div>
            <div className="flex gap-2">
              <Input value={assignee} onChange={(ev) => setAssignee(ev.target.value)} placeholder="处置负责人" />
              <Button size="sm" onClick={() => { patch.mutate({ id: e.id, body: { assignee: assignee || null } }); toast.success('负责人已保存') }}>保存</Button>
            </div>
            <div className="flex gap-2">
              <Textarea value={note} onChange={(ev) => setNote(ev.target.value)} rows={2} />
              <Button size="sm" onClick={() => { patch.mutate({ id: e.id, body: { note: note || null } }); toast.success('备注已保存') }}>保存</Button>
            </div>
            <h3 className="font-medium">处置时间线</h3>
            {(actionRows.data || []).length === 0 ? (
              <p className="text-sm text-muted-foreground">暂无处置记录。</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {(actionRows.data || []).map((a, i) => (
                  <li key={i} className="font-mono text-xs">
                    {fmtTime(a.ts)} · {ACTION_NAMES[a.action] || a.action} · {a.actor} · {fmtPayload(a)}
                  </li>
                ))}
              </ul>
            )}
            <div className="rounded-lg border p-3">
              <h3 className="mb-2 font-medium">训练反馈</h3>
              <select className="mb-2 h-8 w-full rounded-lg border px-2 text-sm" value={taskId} onChange={(ev) => setTaskId(ev.target.value)}>
                <option value="">{confirmed.length ? '选择训练任务' : '没有已确认的训练任务'}</option>
                {confirmed.map((t) => <option key={t.task_id} value={t.task_id}>{`${t.object} · ${t.property} (${t.task_id})`}</option>)}
              </select>
              <div className="flex gap-2">
                {(['false_alarm', 'miss'] as const).map((kind) => (
                  <Button key={kind} variant="outline" size="sm" onClick={async () => {
                    if (!taskId) { toast.error('请先选一个训练任务'); return }
                    try {
                      await api(`/events/${e.id}/feedback`, jsonInit('POST', { task_id: taskId, kind }))
                      toast.success(kind === 'miss' ? '已记为漏报并入库' : '已记为误报并入库')
                      void qc.invalidateQueries({ queryKey: ['events'] })
                    } catch (err) { toast.error((err as Error).message) }
                  }}>{kind === 'miss' ? '这是漏报' : '这是误报'}</Button>
                ))}
              </div>
            </div>
          </div>
        )}
      </DetailDrawer>
    </div>
  )
}
