import { Link, useParams } from 'react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { PageHeader } from '@/components/app/page-header'
import { VideoTile } from '@/components/app/video-wall'
import { api } from '@/lib/api'
import type { Camera } from '@/lib/types'

type Model = { id: number; slot_key: string; status: string; task_id: string }

export function CameraDetailPage() {
  const { id } = useParams()
  const camId = Number(id)
  const qc = useQueryClient()
  const cam = useQuery({
    queryKey: ['camera', camId],
    queryFn: () => api<Camera>(`/cameras/${camId}`),
    enabled: Number.isFinite(camId),
  })
  const models = useQuery({
    queryKey: ['models'],
    queryFn: () => api<Model[]>('/models'),
  })

  const toggle = useMutation({
    mutationFn: (kind: 'start' | 'stop') => api(`/cameras/${camId}/${kind}`, { method: 'POST' }),
    onSuccess: (_d, kind) => {
      toast.success(kind === 'start' ? '已启动' : '已停止')
      void qc.invalidateQueries({ queryKey: ['camera', camId] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const rollback = useMutation({
    mutationFn: (mid: number) => api<{ reason?: string }>(`/models/${mid}/rollback`, { method: 'POST' }),
    onSuccess: (r) => {
      toast.success(r.reason || '已回滚')
      void qc.invalidateQueries({ queryKey: ['models'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  if (!Number.isFinite(camId)) return <p className="text-muted-foreground">缺少摄像头 id</p>
  if (cam.isError) return <p className="text-destructive">{(cam.error as Error).message}</p>
  if (!cam.data) return <p className="text-muted-foreground">加载中…</p>

  const live = models.data?.filter((m) => m.status === 'live') || []

  return (
    <div>
      <p className="mb-2 text-sm">
        <Link to="/cameras" className="text-muted-foreground hover:underline">← 摄像头列表</Link>
      </p>
      <PageHeader
        title={cam.data.name}
        description={`${cam.data.source_type} · ${cam.data.source_uri}`}
        actions={
          cam.data.status === 'running' ? (
            <Button onClick={() => toggle.mutate('stop')}>停止</Button>
          ) : (
            <Button onClick={() => toggle.mutate('start')}>启动</Button>
          )
        }
      />
      <span className={`badge ${cam.data.status}`}>{cam.data.status}</span>
      <h2 className="mt-6 mb-2 text-lg font-medium">直播 / 回放</h2>
      <VideoTile camera={cam.data} />
      <h2 className="mt-6 mb-2 text-lg font-medium">已部署模型</h2>
      {models.isError ? (
        <p className="text-muted-foreground">模型列表失败：{(models.error as Error).message}</p>
      ) : live.length === 0 ? (
        <p className="text-muted-foreground">暂无线上模型。到「模型训练」完成部署。</p>
      ) : (
        live.map((m) => (
          <div key={m.id} className="mb-2 rounded-lg border p-3">
            <div>{m.slot_key} <span className="badge">{m.status}</span></div>
            <div className="font-mono text-xs text-muted-foreground">任务 {m.task_id} · 版本 {m.id}</div>
            <Button className="mt-2" size="sm" variant="outline" onClick={() => rollback.mutate(m.id)}>回滚</Button>
          </div>
        ))
      )}
    </div>
  )
}
