import { useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { PageHeader } from '@/components/app/page-header'
import { DataTable, type Column } from '@/components/app/data-table'
import { api, jsonInit } from '@/lib/api'
import { DIRECTION_NAMES, RULE_TYPE_NAMES } from '@/lib/labels'
import type { Camera, Rule, RulePreset } from '@/lib/types'

type PresetData = {
  presets: RulePreset[]
  common_classes: Array<{ id: string; name: string }>
  classes_note: string
}

const PRESET_CARD: Record<string, string> = {
  zone_intrusion: 'border-red-400/40',
  loitering: 'border-amber-400/40',
  object_count: 'border-blue-400/40',
  zone_count: 'border-violet-400/40',
  line_crossing: 'border-emerald-400/40',
}

export function RulesPage() {
  const [cameras, setCameras] = useState<Camera[]>([])
  const [presetData, setPresetData] = useState<PresetData | null>(null)
  const [camId, setCamId] = useState('')
  const [step, setStep] = useState(1)
  const [preset, setPreset] = useState<RulePreset | null>(null)
  const [values, setValues] = useState<Record<string, unknown>>({})
  const [rules, setRules] = useState<Rule[]>([])
  const [points, setPoints] = useState<number[][]>([])
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const scaleRef = useRef(1)

  useEffect(() => {
    void (async () => {
      try {
        const cams = await api<Camera[]>('/cameras')
        setCameras(cams)
        if (cams[0]) setCamId(String(cams[0].id))
        setPresetData(await api<PresetData>('/api/rules/presets'))
      } catch (err) {
        toast.error((err as Error).message)
      }
    })()
  }, [])

  async function loadRules(id = camId) {
    if (!id) return
    setRules(await api<Rule[]>(`/cameras/${id}/rules`))
  }

  useEffect(() => {
    if (camId) void loadRules(camId)
  }, [camId])

  function pickPreset(p: RulePreset) {
    setPreset(p)
    const next: Record<string, unknown> = {}
    for (const f of p.fields) next[f.key] = f.default
    setValues(next)
    setStep(2)
  }

  function isLine() {
    return preset?.zone_shape === 'line'
  }

  function redraw() {
    const canvas = canvasRef.current
    const img = imgRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx || !img) return
    const scale = scaleRef.current
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    const drawShape = (pts: number[][], color: string, closed: boolean, line: boolean) => {
      if (!pts.length) return
      ctx.strokeStyle = color
      ctx.fillStyle = color + '33'
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.moveTo(pts[0][0] * scale, pts[0][1] * scale)
      for (const [x, y] of pts.slice(1)) ctx.lineTo(x * scale, y * scale)
      if (closed && !line) { ctx.closePath(); ctx.fill() }
      ctx.stroke()
      for (const [x, y] of pts) {
        ctx.fillStyle = color
        ctx.beginPath()
        ctx.arc(x * scale, y * scale, 3, 0, Math.PI * 2)
        ctx.fill()
      }
    }
    for (const rule of rules) {
      const poly = rule.params?.polygon as number[][] | undefined
      const line = rule.params?.line as number[][] | undefined
      if (poly) drawShape(poly, '#3b9eff', true, false)
      if (line) drawShape(line, '#34c77b', false, true)
    }
    drawShape(points, '#e5b545', !isLine(), !!isLine())
  }

  useEffect(() => { redraw() }, [points, rules, step])

  async function loadCanvas() {
    const canvas = canvasRef.current
    if (!canvas) return false
    return new Promise<boolean>((resolve) => {
      const img = new Image()
      img.onload = () => {
        imgRef.current = img
        const maxW = Math.min(900, canvas.parentElement?.clientWidth || 640)
        const scale = Math.min(1, maxW / img.naturalWidth)
        scaleRef.current = scale
        canvas.width = Math.round(img.naturalWidth * scale)
        canvas.height = Math.round(img.naturalHeight * scale)
        setPoints([])
        resolve(true)
      }
      img.onerror = () => {
        toast.error('该摄像头暂无画面（未启动？），先启动摄像头再画区域')
        resolve(false)
      }
      img.src = `/cameras/${camId}/snapshot.jpg?t=${Date.now()}`
    })
  }

  function buildParams() {
    if (!preset) return {}
    const params: Record<string, unknown> = {}
    if (preset.needs_zone) {
      if (preset.zone_shape === 'line') params.line = points
      else params.polygon = points
    }
    for (const f of preset.fields) {
      if (f.key === 'name' || f.key === 'cooldown') continue
      if (f.key === 'active_hours') {
        if (values.active_hours) params.active_hours = values.active_hours
        continue
      }
      if (f.key === 'classes') {
        const cls = values.classes as string[] | string
        if (preset.type === 'object_count') params.class = Array.isArray(cls) ? cls[0] : cls
        else params.classes = Array.isArray(cls) ? cls : [cls]
      } else {
        params[f.key] = values[f.key]
      }
    }
    return params
  }

  async function saveRule() {
    if (!preset) return
    try {
      await api(`/cameras/${camId}/rules`, jsonInit('POST', {
        name: values.name,
        type: preset.type,
        params: buildParams(),
        cooldown: Number(values.cooldown) || 30,
      }))
      toast.success(`规则「${values.name}」已保存`)
      setPoints([])
      setStep(1)
      await loadRules()
    } catch (err) {
      toast.error((err as Error).message)
    }
  }

  function paramSummary(rule: Rule) {
    const p = rule.params || {}
    const parts: string[] = []
    if (rule.type === 'loitering' && p.duration) parts.push(`停留超 ${p.duration} 秒`)
    if (rule.type === 'object_count' && p.threshold) parts.push(`数量超 ${p.threshold} 个`)
    if (rule.type === 'zone_count' && p.threshold) parts.push(`区域内超 ${p.threshold} 人`)
    if (rule.type === 'line_crossing') parts.push(`穿越方向：${DIRECTION_NAMES[String(p.direction || 'both')]}`)
    const cls = p.class || (Array.isArray(p.classes) ? (p.classes as string[]).join('/') : '')
    if (cls) parts.push(`目标：${cls}`)
    if (p.polygon) parts.push(`${(p.polygon as unknown[]).length} 边形区域`)
    if (p.line) parts.push('一条线段')
    if (p.active_hours) parts.push(`${p.active_hours} 生效`)
    return parts.join(' · ')
  }

  const ruleCols: Column<Rule>[] = [
    { header: '规则', cell: ({ row }) => row.original.name || RULE_TYPE_NAMES[row.original.type] },
    { header: '类型', cell: ({ row }) => RULE_TYPE_NAMES[row.original.type] || row.original.type },
    { header: '说明', cell: ({ row }) => <span className="text-muted-foreground">{paramSummary(row.original)}</span> },
    { header: '冷却', cell: ({ row }) => `${row.original.cooldown}s` },
    { header: '启用', cell: ({ row }) => (row.original.enabled ? '是' : '否') },
    {
      header: '',
      cell: ({ row }) => (
        <Button
          size="sm"
          variant="destructive"
          onClick={async () => {
            try {
              await api(`/cameras/${camId}/rules/${row.original.id}`, { method: 'DELETE' })
              toast.success('规则已删除')
              await loadRules()
            } catch (err) {
              toast.error((err as Error).message)
            }
          }}
        >
          删除
        </Button>
      ),
    },
  ]

  const minPts = isLine() ? 2 : 3
  const canSave = isLine() ? points.length === 2 : points.length >= 3

  return (
    <div>
      <PageHeader title="规则" />
      <div className="mb-4 flex items-center gap-2">
        <label className="text-sm">摄像头</label>
        <select
          className="h-8 rounded-lg border bg-background px-2 text-sm"
          value={camId}
          onChange={(e) => { setCamId(e.target.value); setStep(1); setPoints([]) }}
        >
          {cameras.map((c) => (
            <option key={c.id} value={c.id}>{`[${c.id}] ${c.name}`}</option>
          ))}
        </select>
        {cameras.length === 0 ? <span className="text-sm text-muted-foreground">请先添加摄像头</span> : null}
      </div>
      {cameras.length > 0 && presetData ? (
        <>
          <div className="mb-3 flex gap-3 text-sm">
            <span className={step === 1 ? 'font-medium' : 'text-muted-foreground'}>① 选场景</span>
            <span className={step === 2 ? 'font-medium' : 'text-muted-foreground'}>② 填参数</span>
            {preset?.needs_zone !== false ? (
              <span className={step === 3 ? 'font-medium' : 'text-muted-foreground'}>③ 画区域</span>
            ) : null}
          </div>
          {step === 1 ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {presetData.presets.map((p) => (
                <button
                  key={p.type}
                  type="button"
                  className={`rounded-xl border p-4 text-left ${PRESET_CARD[p.type] || ''}`}
                  onClick={() => pickPreset(p)}
                >
                  <h3 className="font-medium">{p.display_name}</h3>
                  <div className="text-sm text-muted-foreground">{p.tagline}</div>
                  <p className="mt-2 text-sm">{p.description}</p>
                </button>
              ))}
            </div>
          ) : null}
          {step === 2 && preset ? (
            <div className="max-w-xl space-y-3 rounded-xl border p-4">
              {preset.fields.map((f) => (
                <div key={f.key} className="grid gap-1">
                  <label className="text-sm">{f.label}</label>
                  {f.kind === 'class' ? (
                    <select
                      className="h-8 rounded-lg border bg-background px-2 text-sm"
                      defaultValue={Array.isArray(f.default) ? String(f.default[0]) : String(f.default)}
                      onChange={(e) => setValues((v) => ({ ...v, [f.key]: [e.target.value] }))}
                    >
                      {presetData.common_classes.map((c) => (
                        <option key={c.id} value={c.id}>{`${c.name}（${c.id}）`}</option>
                      ))}
                    </select>
                  ) : f.kind === 'direction' ? (
                    <select
                      className="h-8 rounded-lg border bg-background px-2 text-sm"
                      defaultValue={String(f.default)}
                      onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                    >
                      {Object.entries(DIRECTION_NAMES).map(([k, n]) => (
                        <option key={k} value={k}>{n}</option>
                      ))}
                    </select>
                  ) : (
                    <Input
                      type={f.kind === 'number' ? 'number' : 'text'}
                      defaultValue={String(f.default ?? '')}
                      onChange={(e) =>
                        setValues((v) => ({
                          ...v,
                          [f.key]: f.kind === 'number' ? Number(e.target.value) : e.target.value,
                        }))
                      }
                    />
                  )}
                  <p className="text-xs text-muted-foreground">{f.hint}{f.unit ? ` · ${f.unit}` : ''}</p>
                </div>
              ))}
              <div className="flex gap-2">
                <Button
                  onClick={async () => {
                    if (preset.needs_zone) {
                      if (!(await loadCanvas())) return
                      setStep(3)
                    } else {
                      await saveRule()
                    }
                  }}
                >
                  下一步
                </Button>
                <Button variant="destructive" onClick={() => setStep(1)}>返回重选</Button>
              </div>
            </div>
          ) : null}
          {step === 3 ? (
            <div className="rounded-xl border p-4">
              <canvas
                ref={canvasRef}
                className="max-w-full cursor-crosshair rounded-md border"
                onClick={(ev) => {
                  const canvas = canvasRef.current
                  if (!canvas) return
                  const rect = canvas.getBoundingClientRect()
                  const scale = scaleRef.current
                  setPoints((pts) => {
                    const next = isLine() && pts.length >= 2 ? [] : [...pts]
                    next.push([
                      Math.round((ev.clientX - rect.left) / scale),
                      Math.round((ev.clientY - rect.top) / scale),
                    ])
                    return next
                  })
                }}
                onDoubleClick={() => {
                  if (points.length >= minPts) toast.success(isLine() ? '线段已完成' : `多边形已闭合（${points.length} 个顶点）`)
                }}
              />
              <p className="mt-2 text-sm text-muted-foreground">
                {isLine()
                  ? '在画布上点击两个点画出计数线；再次点击可重画。方向约定：沿第一点→第二点看，左→右为进。'
                  : '在画布上点击添加顶点，至少 3 个点；双击画布也可闭合。'}
              </p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="outline" disabled={points.length < minPts} onClick={() => toast.success(isLine() ? '线段已完成' : `多边形已闭合（${points.length} 个顶点）`)}>
                  {isLine() ? '完成线段' : '闭合多边形'}
                </Button>
                <Button variant="outline" disabled={!points.length} onClick={() => setPoints((p) => p.slice(0, -1))}>撤销点</Button>
                <Button disabled={!canSave} onClick={() => void saveRule()}>保存规则</Button>
                <Button variant="destructive" onClick={() => setStep(2)}>返回改参数</Button>
              </div>
            </div>
          ) : null}
        </>
      ) : null}
      <h2 className="mt-8 mb-3 text-lg font-medium">已有规则</h2>
      <DataTable columns={ruleCols} data={rules} emptyText="该摄像头还没有规则，从上面的场景卡片开始吧。" />
    </div>
  )
}
