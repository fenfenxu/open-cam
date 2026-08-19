export type Camera = {
  id: number
  name: string
  source_type: 'file' | 'rtsp' | string
  source_uri: string
  status: string
}

export type VideoFile = {
  id: number
  filename: string
  size_bytes: number
  duration_sec?: number | null
  width?: number | null
  height?: number | null
}

export type EventItem = {
  id: number
  ts: number
  camera_id: number
  type: string
  confidence: number
  detail: unknown
  snapshot_path?: string | null
  vlm_status: string
  vlm_verdict?: string | null
  vlm_reason?: string | null
  acked: boolean
  status?: string
  starred?: boolean
  assignee?: string | null
  note?: string | null
  camera_name?: string | null
  source_filename?: string | null
  source_offset?: number | null
  clip_start?: number
  clip_end?: number
}

export type Rule = {
  id: number
  name?: string | null
  type: string
  params: Record<string, unknown>
  cooldown: number
  enabled: boolean
}

export type RulePreset = {
  type: string
  display_name: string
  tagline: string
  description: string
  scenarios: string[]
  needs_zone: boolean
  zone_shape?: string
  fields: Array<{
    key: string
    label: string
    kind: string
    hint: string
    unit?: string
    default: unknown
  }>
}
