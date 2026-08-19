import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { PageHeader } from '@/components/app/page-header'
import { api, fmtTime } from '@/lib/api'
import type { Camera, EventItem } from '@/lib/types'

function drawFootfall(canvas: HTMLCanvasElement, buckets: Array<{ in: number; out: number }>) {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  const W = canvas.width
  const H = canvas.height
  ctx.clearRect(0, 0, W, H)
  const max = Math.max(1, ...buckets.map((b) => Math.max(b.in, b.out)))
  const groupW = W / 24
  const barW = Math.max(1, (groupW - 3) / 2)
  buckets.forEach((b, i) => {
    const x0 = i * groupW + 1
    const inH = (b.in / max) * (H - 12)
    const outH = (b.out / max) * (H - 12)
    ctx.fillStyle = '#3b9eff'
    ctx.fillRect(x0, H - inH, barW, inH)
    ctx.fillStyle = '#e5b545'
    ctx.fillRect(x0 + barW + 1, H - outH, barW, outH)
  })
  ctx.fillStyle = '#8a94a3'
  ctx.font = '9px sans-serif'
  ctx.fillText('0', 1, H - 1)
  ctx.fillText('12', 12 * groupW, H - 1)
  ctx.fillText('23', 23 * groupW, H - 1)
}

function CameraCard({ cam }: { cam: Camera }) {
  const imgRef = useRef<HTMLImageElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const countRef = useRef<HTMLSpanElement>(null)
  const lastRef = useRef<HTMLSpanElement>(null)
  const totalRef = useRef<HTMLSpanElement>(null)
  const emptyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const timers: number[] = []
    const refreshShot = () => {
      if (cam.status === 'running' && imgRef.current) {
        imgRef.current.src = `/cameras/${cam.id}/snapshot.jpg?t=${Date.now()}`
      }
    }
    refreshShot()
    timers.push(window.setInterval(refreshShot, 1000))
    const refreshEvents = async () => {
      try {
        const events = await api<EventItem[]>(`/events?camera_id=${cam.id}&limit=50`)
        if (countRef.current) countRef.current.textContent = String(events.length)
        if (lastRef.current) lastRef.current.textContent = events.length ? fmtTime(events[0].ts) : '无'
      } catch { /* ignore */ }
    }
    void refreshEvents()
    timers.push(window.setInterval(() => void refreshEvents(), 5000))
    const refreshFoot = async () => {
      try {
        const data = await api<{ total_in: number; total_out: number; buckets: Array<{ in: number; out: number }> }>(
          `/api/stats/footfall?camera_id=${cam.id}`,
        )
        const total = data.total_in + data.total_out
        if (totalRef.current) totalRef.current.textContent = total ? `进 ${data.total_in} / 出 ${data.total_out}` : ''
        if (emptyRef.current) emptyRef.current.hidden = total > 0
        if (canvasRef.current) {
          canvasRef.current.hidden = total === 0
          if (total) drawFootfall(canvasRef.current, data.buckets)
        }
      } catch { /* ignore */ }
    }
    void refreshFoot()
    timers.push(window.setInterval(() => void refreshFoot(), 30000))
    return () => timers.forEach(clearInterval)
  }, [cam.id, cam.status])

  return (
    <Link to={`/cameras/${cam.id}`} className="block rounded-xl border bg-card p-4 hover:bg-muted/30">
      <h3 className="font-medium">
        {cam.name} <span className="badge ml-1 text-xs">{cam.status}</span>
      </h3>
      <div className="mt-1 font-mono text-xs text-muted-foreground">
        {cam.source_type} · {cam.source_uri}
      </div>
      <img ref={imgRef} className="cam-shot mt-2 aspect-video w-full rounded-md bg-muted object-contain" alt="暂无画面" />
      <div className="mt-2 text-sm text-muted-foreground">
        最近事件：<span ref={countRef}>—</span> · 最新：<span ref={lastRef}>—</span>
      </div>
      <div className="mt-2 text-sm">
        今日客流 <span ref={totalRef} />{' '}
        <span style={{ color: '#3b9eff' }}>■ 进</span>{' '}
        <span style={{ color: '#e5b545' }}>■ 出</span>
      </div>
      <canvas ref={canvasRef} className="mt-1" width={260} height={64} />
      <div ref={emptyRef} className="text-xs text-muted-foreground" hidden>
        暂无客流数据（先配置「越线计数」规则）
      </div>
    </Link>
  )
}

export function DashboardPage() {
  const cameras = useQuery({ queryKey: ['cameras'], queryFn: () => api<Camera[]>('/cameras') })
  return (
    <div>
      <PageHeader title="仪表盘" />
      {cameras.isError ? (
        <p className="text-destructive">{(cameras.error as Error).message}</p>
      ) : !cameras.data?.length ? (
        <p className="text-muted-foreground">还没有摄像头，去「摄像头」页添加一路。</p>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {cameras.data.map((cam) => (
            <CameraCard key={cam.id} cam={cam} />
          ))}
        </div>
      )}
    </div>
  )
}
