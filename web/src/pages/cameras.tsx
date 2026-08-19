import { useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { z } from 'zod'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { DataTable, type Column } from '@/components/app/data-table'
import { PageHeader } from '@/components/app/page-header'
import { api, ApiError, jsonInit } from '@/lib/api'
import type { Camera, VideoFile } from '@/lib/types'

const createSchema = z.object({
  name: z.string().min(1, '请填写名称'),
  source_uri: z.string().min(1, '请填写源地址'),
})

function dash(value: unknown) {
  return value == null || value === '' ? '—' : String(value)
}

export function CamerasPage() {
  const nav = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [sourceType, setSourceType] = useState<'file' | 'rtsp'>('file')
  const [uri, setUri] = useState('')
  const [autostart, setAutostart] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const cameras = useQuery({ queryKey: ['cameras'], queryFn: () => api<Camera[]>('/cameras') })
  const videos = useQuery({ queryKey: ['videos'], queryFn: () => api<VideoFile[]>('/videos') })

  const create = useMutation({
    mutationFn: () => {
      const parsed = createSchema.safeParse({ name, source_uri: uri })
      if (!parsed.success) throw new Error(parsed.error.issues[0]?.message || '校验失败')
      return api('/cameras', jsonInit('POST', {
        name: parsed.data.name,
        source_type: sourceType,
        source_uri: parsed.data.source_uri,
        autostart,
      }))
    },
    onSuccess: () => {
      toast.success('摄像头已添加')
      setOpen(false)
      setName('')
      setUri('')
      void qc.invalidateQueries({ queryKey: ['cameras'] })
    },
    onError: (err: Error) => toast.error(err instanceof ApiError ? err.message : err.message),
  })

  const saveName = useMutation({
    mutationFn: ({ id, name: n }: { id: number; name: string }) =>
      api(`/cameras/${id}`, jsonInit('PUT', { name: n })),
    onSuccess: () => {
      toast.success('已保存')
      void qc.invalidateQueries({ queryKey: ['cameras'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const act = useMutation({
    mutationFn: ({ id, kind }: { id: number; kind: 'start' | 'stop' | 'del' }) =>
      kind === 'del'
        ? api(`/cameras/${id}`, { method: 'DELETE' })
        : api(`/cameras/${id}/${kind}`, { method: 'POST' }),
    onSuccess: (_d, vars) => {
      toast.success(vars.kind === 'del' ? '已删除' : vars.kind === 'start' ? '已启动' : '已停止')
      void qc.invalidateQueries({ queryKey: ['cameras'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const delVideo = useMutation({
    mutationFn: (id: number) => api(`/videos/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      toast.success('视频已删除')
      void qc.invalidateQueries({ queryKey: ['videos'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const columns = useMemo<Column<Camera & { _name?: string }>[]>(
    () => [
      { accessorKey: 'id', header: 'ID' },
      {
        header: '名称',
        cell: ({ row }) => (
          <Input
            className="c-name h-8 w-40"
            data-id={row.original.id}
            defaultValue={row.original.name}
            onBlur={(e) => {
              const next = e.target.value.trim()
              if (next && next !== row.original.name) saveName.mutate({ id: row.original.id, name: next })
            }}
          />
        ),
      },
      {
        header: '类型',
        cell: ({ row }) => (row.original.source_type === 'file' ? '视频文件' : 'RTSP 流'),
      },
      {
        header: '源地址',
        cell: ({ row }) => <span className="font-mono text-xs">{row.original.source_uri}</span>,
      },
      {
        header: '状态',
        cell: ({ row }) => <span className="badge">{row.original.status}</span>,
      },
      {
        header: '操作',
        cell: ({ row }) => (
          <div className="flex flex-wrap gap-1" onClick={(e) => e.stopPropagation()}>
            <Button size="sm" variant="outline" onClick={() => nav(`/cameras/${row.original.id}`)}>
              查看
            </Button>
            {row.original.status === 'running' ? (
              <Button size="sm" variant="outline" onClick={() => act.mutate({ id: row.original.id, kind: 'stop' })}>
                停止
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => act.mutate({ id: row.original.id, kind: 'start' })}>
                启动
              </Button>
            )}
            <Button
              size="sm"
              variant="outline"
              data-act="save"
              onClick={() => {
                const input = document.querySelector<HTMLInputElement>(`.c-name[data-id="${row.original.id}"]`)
                saveName.mutate({ id: row.original.id, name: input?.value || row.original.name })
              }}
            >
              保存
            </Button>
            <Button size="sm" variant="destructive" onClick={() => act.mutate({ id: row.original.id, kind: 'del' })}>
              删除
            </Button>
          </div>
        ),
      },
    ],
    [act, nav, saveName],
  )

  const videoCols = useMemo<Column<VideoFile>[]>(
    () => [
      { accessorKey: 'id', header: 'ID' },
      { accessorKey: 'filename', header: '文件名' },
      { accessorKey: 'size_bytes', header: '大小' },
      { header: '时长', cell: ({ row }) => dash(row.original.duration_sec) },
      {
        header: '分辨率',
        cell: ({ row }) =>
          row.original.width && row.original.height ? `${row.original.width}×${row.original.height}` : '—',
      },
      {
        header: '操作',
        cell: ({ row }) => (
          <Button size="sm" variant="destructive" onClick={() => delVideo.mutate(row.original.id)}>
            删除
          </Button>
        ),
      },
    ],
    [delVideo],
  )

  async function upload(file: File) {
    const form = new FormData()
    form.append('file', file)
    try {
      const resp = await fetch('/cameras/upload', { method: 'POST', body: form })
      const body = await resp.json()
      if (!resp.ok) throw new Error(body.detail || `HTTP ${resp.status}`)
      setUri(body.path)
      toast.success('文件已上传')
      void qc.invalidateQueries({ queryKey: ['videos'] })
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  return (
    <div>
      <PageHeader
        title="摄像头"
        actions={<Button onClick={() => setOpen(true)}>添加摄像头</Button>}
      />
      <p className="mb-4 text-sm text-muted-foreground">
        已创建的摄像头只能改名称；更换类型或视频源请新建。
      </p>
      <DataTable columns={columns} data={cameras.data || []} emptyText="暂无摄像头。" />
      <h2 className="mt-8 mb-3 text-lg font-medium">已上传视频</h2>
      <DataTable columns={videoCols} data={videos.data || []} emptyText="暂无已上传视频。" />

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>添加摄像头</DialogTitle>
            <DialogDescription>接入一路视频文件或 RTSP 流。</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="grid gap-1">
              <Label htmlFor="c-name">名称</Label>
              <Input id="c-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="门口" />
            </div>
            <div className="grid gap-1">
              <Label>类型</Label>
              <select
                className="h-8 rounded-lg border bg-background px-2 text-sm"
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value as 'file' | 'rtsp')}
              >
                <option value="file">视频文件</option>
                <option value="rtsp">RTSP 流</option>
              </select>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="c-uri">源地址</Label>
              <Input
                id="c-uri"
                value={uri}
                onChange={(e) => setUri(e.target.value)}
                placeholder="/path/to/video.mp4 或 rtsp://..."
              />
            </div>
            {sourceType === 'file' ? (
              <div>
                <input
                  ref={fileRef}
                  type="file"
                  accept="video/*,.mkv,.ts"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0]
                    if (f) void upload(f)
                    e.target.value = ''
                  }}
                />
                <Button type="button" variant="outline" onClick={() => fileRef.current?.click()}>
                  选择文件…
                </Button>
              </div>
            ) : null}
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={autostart}
                onCheckedChange={(v) => setAutostart(v === true)}
              />
              创建即启动
            </label>
          </div>
          <DialogFooter>
            <Button onClick={() => create.mutate()}>添加</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
