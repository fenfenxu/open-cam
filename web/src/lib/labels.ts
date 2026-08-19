export const RULE_TYPE_NAMES: Record<string, string> = {
  zone_intrusion: '区域入侵',
  loitering: '徘徊逗留',
  object_count: '人数统计',
  zone_count: '区域人数',
  line_crossing: '越线计数',
}

export const STATUS_NAMES: Record<string, string> = {
  open: '待处理',
  acked: '已确认',
  resolved: '已处置',
  ignored: '已忽略',
}

export const ACTION_NAMES: Record<string, string> = {
  star: '加关注',
  unstar: '取消关注',
  assign: '指派负责人',
  status: '状态流转',
  note: '备注',
  ack: '确认',
  notify: '通知推送',
}

export const NEXT_ACTIONS: Record<string, [string, string][]> = {
  open: [['acked', '确认'], ['resolved', '处置完成'], ['ignored', '误报忽略']],
  acked: [['resolved', '处置完成'], ['ignored', '误报忽略']],
  resolved: [['open', '重新打开']],
  ignored: [['open', '重新打开']],
}

export const DIRECTION_NAMES: Record<string, string> = {
  both: '双向',
  in: '仅进',
  out: '仅出',
}

export function eventStatus(event: { acked?: boolean; status?: string }): string {
  if (event.status) return event.status
  return event.acked ? 'acked' : 'open'
}

export function fmtMediaTime(sec: number | null | undefined): string {
  if (sec == null || Number.isNaN(Number(sec))) return '—'
  const s = Math.max(0, Number(sec))
  const m = Math.floor(s / 60)
  const rest = (s - m * 60).toFixed(2).padStart(5, '0')
  return `${String(m).padStart(2, '0')}:${rest}`
}

export function fmtClipRange(event: { source_offset?: number | null; clip_start?: number; clip_end?: number }): string {
  if (event.source_offset == null) return '—'
  return `${fmtMediaTime(event.clip_start)} – ${fmtMediaTime(event.clip_end)}`
}
