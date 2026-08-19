import { useEffect, useRef, useState, type MutableRefObject } from 'react'
import { useNavigate, useParams } from 'react-router'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { PageHeader } from '@/components/app/page-header'
import { api, jsonInit } from '@/lib/api'
import type { Camera, VideoFile } from '@/lib/types'

const STEPS = [
  { id: 1, title: '说需求' },
  { id: 2, title: '确认定义' },
  { id: 3, title: '选视频源' },
  { id: 4, title: '自动标注' },
  { id: 5, title: '训练' },
  { id: 6, title: '评估' },
  { id: 7, title: '部署' },
]

type Task = {
  task_id: string
  goal?: string
  object?: string
  property?: string
  status?: string
  definition?: {
    object?: string
    property?: string
    classes?: string[]
    rule?: { trigger?: string }
    metrics?: unknown
    region?: unknown
  }
  metrics_explained?: string
  frames?: number
  samples?: { review?: number; total?: number }
  train?: { status?: string; result?: { conclusion?: string; suggestions?: string[] }; error?: string }
}

function inferStep(task: Task | null) {
  if (!task) return 1
  const train = task.train || {}
  if (train.status === 'done' || train.result) return 6
  if (train.status === 'running') return 5
  if ((task.samples?.review || 0) > 0 || (task.samples?.total || 0) > 0) return 4
  if ((task.frames || 0) > 0) return 3
  if (task.status === 'confirmed') return 3
  return 2
}

export function TrainingPage() {
  const { id } = useParams()
  const nav = useNavigate()
  const [cameras, setCameras] = useState<Camera[]>([])
  const [videos, setVideos] = useState<VideoFile[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [taskId, setTaskId] = useState<string | null>(id || null)
  const [task, setTask] = useState<Task | null>(null)
  const [step, setStep] = useState(1)
  const pollRef = useRef<number | null>(null)

  useEffect(() => {
    return () => { if (pollRef.current) window.clearTimeout(pollRef.current) }
  }, [])

  useEffect(() => {
    void (async () => {
      try {
        setCameras(await api('/cameras'))
        setVideos(await api<VideoFile[]>('/videos').catch((): VideoFile[] => []))
        const list = await api<Task[]>('/training/tasks')
        setTasks(list)
        if (id) {
          const t = await api<Task>(`/training/tasks/${id}`)
          setTask(t)
          setTaskId(t.task_id)
          setStep(inferStep(t))
        }
      } catch (err) {
        toast.error((err as Error).message)
      }
    })()
  }, [id])

  async function refreshTask(tid = taskId) {
    if (!tid) return
    const t = await api<Task>(`/training/tasks/${tid}`)
    setTask(t)
    setTaskId(tid)
    nav(`/training/${tid}`)
    return t
  }

  return (
    <div>
      <PageHeader title="模型训练" description="用一句话描述想监控的状态，系统帮你抽帧、标注、训练并部署本地小模型。" />
      <div className="mb-4 flex items-center gap-2 rounded-xl border p-3">
        <label className="text-sm">已有任务</label>
        <select
          className="h-8 rounded-lg border bg-background px-2 text-sm"
          value={taskId || ''}
          onChange={(e) => setTaskId(e.target.value || null)}
        >
          <option value="">新任务</option>
          {tasks.map((t) => (
            <option key={t.task_id} value={t.task_id}>{`${t.object || t.task_id} · ${t.property || t.status}`}</option>
          ))}
        </select>
        <Button
          variant="outline"
          onClick={async () => {
            if (!taskId) { setTask(null); setStep(1); nav('/training'); return }
            await refreshTask(taskId)
            const t = await api<Task>(`/training/tasks/${taskId}`)
            setStep(inferStep(t))
          }}
        >
          打开
        </Button>
      </div>
      <div className="mb-4 flex flex-wrap gap-2 text-sm">
        {STEPS.map((s) => (
          <button
            key={s.id}
            type="button"
            className={s.id === step ? 'font-medium' : 'text-muted-foreground'}
            onClick={() => setStep(s.id)}
          >
            {s.id}. {s.title}
          </button>
        ))}
      </div>
      <div className="rounded-xl border p-4">
        {step === 1 ? (
          <Step1
            task={task}
            onCreated={async (created) => {
              setTask(created)
              setTaskId(created.task_id)
              setTasks(await api('/training/tasks'))
              nav(`/training/${created.task_id}`)
              setStep(2)
            }}
          />
        ) : !taskId || !task ? (
          <p className="text-muted-foreground">请先完成第一步。</p>
        ) : step === 2 ? (
          <Step2 task={task} onBack={() => setStep(1)} onNext={async () => { await refreshTask(); setStep(3) }} />
        ) : step === 3 ? (
          <Step3
            task={task}
            cameras={cameras}
            videos={videos}
            onBack={() => setStep(2)}
            onNext={() => setStep(4)}
            onRefresh={() => void refreshTask()}
          />
        ) : step === 4 ? (
          <Step4 taskId={taskId} onBack={() => setStep(3)} onNext={() => setStep(5)} onRefresh={() => void refreshTask()} />
        ) : step === 5 ? (
          <Step5
            task={task}
            taskId={taskId}
            pollRef={pollRef}
            onBack={() => setStep(4)}
            onDone={async () => { await refreshTask(); setStep(6) }}
          />
        ) : step === 6 ? (
          <Step6 task={task} onBack={() => setStep(5)} onNext={() => setStep(7)} />
        ) : (
          <Step7 taskId={taskId} onBack={() => setStep(6)} onRefresh={() => void refreshTask()} />
        )}
      </div>
    </div>
  )
}

function Step1({ task, onCreated }: { task: Task | null; onCreated: (t: Task) => void }) {
  const [goal, setGoal] = useState(task?.goal || '')
  return (
    <div>
      <h3 className="mb-2 font-medium">① 说需求</h3>
      <p className="mb-2 text-sm text-muted-foreground">例如：「垃圾桶快满了就提醒我」</p>
      <Textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
      <Button
        className="mt-3"
        onClick={async () => {
          if (!goal.trim()) { toast.error('请先写一句需求'); return }
          try {
            const created = await api<Task>('/training/tasks', jsonInit('POST', { goal: goal.trim(), task_id: task?.task_id }))
            onCreated(created)
          } catch (err) { toast.error((err as Error).message) }
        }}
      >
        生成任务定义
      </Button>
    </div>
  )
}

function Step2({ task, onBack, onNext }: { task: Task; onBack: () => void; onNext: () => void }) {
  const d = task.definition || {}
  const [object, setObject] = useState(d.object || '')
  const [property, setProperty] = useState(d.property || '')
  const [classes, setClasses] = useState((d.classes || []).join(', '))
  const [trigger, setTrigger] = useState(d.rule?.trigger || '')
  return (
    <div className="space-y-3">
      <h3 className="font-medium">② 确认定义</h3>
      <p className="text-sm text-muted-foreground">{task.metrics_explained || ''}</p>
      <label className="block text-sm">对象<Input value={object} onChange={(e) => setObject(e.target.value)} /></label>
      <label className="block text-sm">属性<Input value={property} onChange={(e) => setProperty(e.target.value)} /></label>
      <label className="block text-sm">类别（逗号分隔）<Input value={classes} onChange={(e) => setClasses(e.target.value)} /></label>
      <label className="block text-sm">告警触发<Input value={trigger} onChange={(e) => setTrigger(e.target.value)} /></label>
      <div className="flex gap-2">
        <Button
          onClick={async () => {
            const definition = {
              object: object.trim(),
              property: property.trim(),
              classes: classes.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
              rule: { type: 'state_alert', trigger: trigger.trim() },
              metrics: d.metrics,
              region: d.region,
              goal: task.goal,
            }
            try {
              await api(`/training/tasks/${task.task_id}/confirm`, jsonInit('POST', { definition }))
              onNext()
            } catch (err) { toast.error((err as Error).message) }
          }}
        >
          确认并进入下一步
        </Button>
        <Button variant="destructive" onClick={onBack}>返回改需求</Button>
      </div>
    </div>
  )
}

function Step3({
  task, cameras, videos, onBack, onNext, onRefresh,
}: {
  task: Task
  cameras: Camera[]
  videos: VideoFile[]
  onBack: () => void
  onNext: () => void
  onRefresh: () => void
}) {
  const [cam, setCam] = useState('')
  const [vid, setVid] = useState('')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const points = useRef<number[][]>([])
  const imgRef = useRef<HTMLImageElement | null>(null)

  useEffect(() => {
    if (!task.frames || !canvasRef.current) return
    const img = new Image()
    img.onload = () => {
      const canvas = canvasRef.current
      if (!canvas) return
      canvas.width = img.width
      canvas.height = img.height
      canvas.getContext('2d')?.drawImage(img, 0, 0)
      imgRef.current = img
    }
    img.src = `/training/tasks/${task.task_id}/preview.jpg?t=${Date.now()}`
  }, [task.frames, task.task_id])

  return (
    <div>
      <h3 className="mb-2 font-medium">③ 选视频源并抽帧</h3>
      <div className="mb-2 flex flex-wrap gap-2">
        <select className="h-8 rounded-lg border px-2 text-sm" value={cam} onChange={(e) => setCam(e.target.value)}>
          <option value="">—</option>
          {cameras.map((c) => <option key={c.id} value={c.id}>{`[${c.id}] ${c.name}`}</option>)}
        </select>
        <select className="h-8 rounded-lg border px-2 text-sm" value={vid} onChange={(e) => setVid(e.target.value)}>
          <option value="">—</option>
          {videos.map((v) => <option key={v.id} value={v.id}>{v.filename}</option>)}
        </select>
        <Button
          onClick={async () => {
            const payload = cam ? { camera_id: Number(cam) } : vid ? { video_id: Number(vid) } : null
            if (!payload) { toast.error('请选择摄像头或视频'); return }
            try {
              const r = await api<{ written?: number }>(`/training/tasks/${task.task_id}/frames`, jsonInit('POST', payload))
              toast.success(`抽了 ${r.written ?? 0} 帧`)
              onRefresh()
            } catch (err) { toast.error((err as Error).message) }
          }}
        >
          抽帧
        </Button>
      </div>
      <p className="mb-2 text-sm text-muted-foreground">已抽 {task.frames || 0} 张。抽帧后可在画面上点出垃圾桶所在区域。</p>
      <canvas
        ref={canvasRef}
        className="max-w-full border"
        onClick={(ev) => {
          const canvas = canvasRef.current
          const img = imgRef.current
          const ctx = canvas?.getContext('2d')
          if (!canvas || !ctx || !img) return
          const r = canvas.getBoundingClientRect()
          const x = (ev.clientX - r.left) * (canvas.width / r.width)
          const y = (ev.clientY - r.top) * (canvas.height / r.height)
          points.current.push([x, y])
          ctx.drawImage(img, 0, 0)
          ctx.strokeStyle = '#3b9eff'
          ctx.lineWidth = 2
          ctx.beginPath()
          points.current.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])))
          ctx.stroke()
        }}
      />
      <div className="mt-3 flex gap-2">
        <Button
          disabled={!task.frames}
          onClick={async () => {
            if (points.current.length < 3) { toast.error('至少点 3 个顶点'); return }
            try {
              await api(`/training/tasks/${task.task_id}/region`, jsonInit('PUT', { region: points.current }))
              toast.success('区域已保存')
              onRefresh()
            } catch (err) { toast.error((err as Error).message) }
          }}
        >
          保存区域
        </Button>
        <Button variant="outline" onClick={onNext}>下一步：自动标注</Button>
        <Button variant="destructive" onClick={onBack}>返回</Button>
      </div>
    </div>
  )
}

function Step4({ taskId, onBack, onNext, onRefresh }: { taskId: string; onBack: () => void; onNext: () => void; onRefresh: () => void }) {
  const [stat, setStat] = useState('')
  const [item, setItem] = useState<{ id: string; suggested_label?: string; confidence: number; reason?: string; classes: string[] } | null>(null)

  async function loadReview() {
    const q = await api<{ remaining: number; items: Array<{ id: string; suggested_label?: string; confidence: number; reason?: string; classes: string[] }> }>(
      `/training/tasks/${taskId}/review`,
    )
    setStat(`待确认 ${q.remaining} 张`)
    setItem(q.items[0] || null)
  }

  useEffect(() => { void loadReview().catch((err) => toast.error((err as Error).message)) }, [taskId])

  return (
    <div>
      <h3 className="mb-2 font-medium">④ 自动标注 + 人工确认</h3>
      <p className="mb-2 text-sm text-muted-foreground">高置信样本自动入库；不确定的只需点类别或跳过。</p>
      <div className="mb-3 flex items-center gap-2">
        <Button
          onClick={async () => {
            try {
              const r = await api<{ auto: number; review: number }>(`/training/tasks/${taskId}/annotate`, { method: 'POST' })
              toast.success(`自动 ${r.auto}，待确认 ${r.review}`)
              onRefresh()
              await loadReview()
            } catch (err) { toast.error((err as Error).message) }
          }}
        >
          开始标注
        </Button>
        <span className="text-sm text-muted-foreground">{stat}</span>
      </div>
      {!item ? (
        <p className="text-muted-foreground">队列已空，可以去训练。</p>
      ) : (
        <div>
          <img className="max-w-md rounded border" src={`/training/tasks/${taskId}/crop/${item.id}.jpg`} alt="裁剪" />
          <p className="mt-2 text-sm text-muted-foreground">
            建议：{item.suggested_label || '无'}（{item.confidence.toFixed(2)}） {item.reason || ''}
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            {item.classes.map((c) => (
              <Button key={c} variant="outline" onClick={async () => {
                await api(`/training/tasks/${taskId}/review/${item.id}`, jsonInit('POST', { action: 'confirm', label: c }))
                await loadReview()
              }}>{c}</Button>
            ))}
            <Button variant="destructive" onClick={async () => {
              await api(`/training/tasks/${taskId}/review/${item.id}`, jsonInit('POST', { action: 'skip' }))
              await loadReview()
            }}>跳过</Button>
          </div>
        </div>
      )}
      <div className="mt-4 flex gap-2">
        <Button variant="outline" onClick={onNext}>下一步：训练</Button>
        <Button variant="destructive" onClick={onBack}>返回</Button>
      </div>
    </div>
  )
}

function Step5({
  task, taskId, pollRef, onBack, onDone,
}: {
  task: Task
  taskId: string
  pollRef: MutableRefObject<number | null>
  onBack: () => void
  onDone: () => void
}) {
  const [stat, setStat] = useState(task.train?.status || 'idle')

  async function poll() {
    const s = await api<{ status: string; error?: string }>(`/training/tasks/${taskId}/train`)
    setStat(s.status + (s.error ? ` · ${s.error}` : ''))
    if (s.status === 'done') { onDone(); return }
    if (s.status === 'failed') toast.error(s.error || '训练失败')
    if (s.status === 'running') pollRef.current = window.setTimeout(() => void poll(), 1500)
  }

  useEffect(() => {
    if (task.train?.status === 'running') void poll()
  }, [])

  return (
    <div>
      <h3 className="mb-2 font-medium">⑤ 训练</h3>
      <p className="mb-2 text-sm text-muted-foreground">从预训练 YOLO 微调固定区域分类。训练在后台执行。</p>
      <div className="flex items-center gap-2">
        <Button onClick={async () => {
          try {
            await api(`/training/tasks/${taskId}/train`, jsonInit('POST', { epochs: 20 }))
            toast.success('已开始训练')
            await poll()
          } catch (err) { toast.error((err as Error).message) }
        }}>开始训练</Button>
        <span className="text-sm text-muted-foreground">{stat}</span>
      </div>
      <div className="mt-4 flex gap-2">
        <Button variant="outline" onClick={onDone}>查看评估</Button>
        <Button variant="destructive" onClick={onBack}>返回</Button>
      </div>
    </div>
  )
}

function Step6({ task, onBack, onNext }: { task: Task; onBack: () => void; onNext: () => void }) {
  const report = task.train?.result || {}
  return (
    <div>
      <h3 className="mb-2 font-medium">⑥ 评估报告</h3>
      <p>{report.conclusion || '还没有评估报告，请先完成训练。'}</p>
      <pre className="mt-2 whitespace-pre-wrap text-sm text-muted-foreground">
        {(report.suggestions || []).map((s) => `· ${s}`).join('\n')}
      </pre>
      <div className="mt-4 flex gap-2">
        <Button onClick={onNext}>去部署</Button>
        <Button variant="destructive" onClick={onBack}>返回</Button>
      </div>
    </div>
  )
}

function Step7({ taskId, onBack, onRefresh }: { taskId: string; onBack: () => void; onRefresh: () => void }) {
  const [models, setModels] = useState<Array<{ id: number; slot_key: string; status: string }>>([])
  useEffect(() => {
    void api<Array<{ id: number; slot_key: string; status: string }>>(`/models?task_id=${encodeURIComponent(taskId)}`)
      .then(setModels)
      .catch(() => setModels([]))
  }, [taskId])

  async function deploy(id: number, force: boolean) {
    const r = await api<{ reason?: string }>(`/models/${id}/deploy`, jsonInit('POST', { force }))
    toast.success(r.reason || '已部署')
    onRefresh()
    setModels(await api(`/models?task_id=${encodeURIComponent(taskId)}`))
  }

  return (
    <div>
      <h3 className="mb-2 font-medium">⑦ 一键部署 / 回滚</h3>
      <p className="mb-3 text-sm text-muted-foreground">登记本任务最新模型，与线上指标对比后再替换；回滚入口常驻。</p>
      <div className="flex gap-2">
        <Button onClick={async () => {
          try {
            const m = await api<{ id: number }>('/models', jsonInit('POST', { task_id: taskId }))
            await deploy(m.id, false)
          } catch (err) { toast.error((err as Error).message) }
        }}>登记并部署</Button>
        <Button variant="destructive" onClick={async () => {
          try {
            const m = await api<{ id: number }>('/models', jsonInit('POST', { task_id: taskId }))
            await deploy(m.id, true)
          } catch (err) { toast.error((err as Error).message) }
        }}>强制部署</Button>
      </div>
      <div className="mt-4">
        {models.length ? models.map((m) => (
          <div key={m.id} className="mb-2 flex items-center gap-3 rounded border p-2 text-sm">
            <span className="font-mono">{m.id}</span>
            <span>{m.slot_key}</span>
            <span>{m.status}</span>
            {m.status === 'live' ? (
              <Button size="sm" variant="outline" onClick={async () => {
                const r = await api<{ reason?: string }>(`/models/${m.id}/rollback`, { method: 'POST' })
                toast.success(r.reason || '已回滚')
                setModels(await api(`/models?task_id=${encodeURIComponent(taskId)}`))
              }}>回滚</Button>
            ) : (
              <Button size="sm" onClick={() => void deploy(m.id, false)}>部署</Button>
            )}
          </div>
        )) : <p className="text-muted-foreground">还没有登记模型。</p>}
      </div>
      <Button className="mt-4" variant="destructive" onClick={onBack}>返回</Button>
    </div>
  )
}
