import { useEffect, useRef, type ReactNode } from 'react'
import { Link } from 'react-router'
import type { Camera } from '@/lib/types'
import { cn } from '@/lib/utils'

export function VideoWall({
  cameras,
  className,
}: {
  cameras: Camera[]
  className?: string
}) {
  return (
    <div className={cn('grid gap-4 sm:grid-cols-2 xl:grid-cols-3', className)}>
      {cameras.map((cam) => (
        <VideoTile key={cam.id} camera={cam} />
      ))}
    </div>
  )
}

export function VideoTile({
  camera,
  live,
  replay,
}: {
  camera: Camera
  live?: boolean
  replay?: boolean
}) {
  const showLive = live ?? camera.status === 'running'
  const showReplay = replay ?? camera.source_type === 'file'
  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      <LivePane camera={camera} running={showLive} />
      <div className="border-t p-3">
        <ReplayPane camera={camera} isFile={showReplay} />
      </div>
    </div>
  )
}

function LivePane({ camera, running }: { camera: Camera; running: boolean }) {
  const imgRef = useRef<HTMLImageElement>(null)
  useEffect(() => {
    return () => {
      if (imgRef.current) imgRef.current.src = ''
    }
  }, [])
  if (running) {
    return (
      <img
        ref={imgRef}
        className="cam-live aspect-video w-full bg-muted object-contain"
        alt="直播"
        src={`/cameras/${camera.id}/live.mjpg`}
      />
    )
  }
  return (
    <div className="flex aspect-video items-center justify-center bg-muted text-sm text-muted-foreground">
      摄像头未运行。
      <img
        className="cam-shot hidden max-h-full"
        alt="暂无画面"
        src={`/cameras/${camera.id}/snapshot.jpg`}
        onError={(e) => {
          e.currentTarget.style.display = 'none'
        }}
        onLoad={(e) => {
          e.currentTarget.style.display = 'block'
          e.currentTarget.parentElement?.querySelector('span')?.remove()
        }}
      />
    </div>
  )
}

function ReplayPane({ camera, isFile }: { camera: Camera; isFile: boolean }) {
  if (isFile) {
    return (
      <video
        className="cam-replay w-full"
        controls
        playsInline
        src={`/cameras/${camera.id}/source`}
        onError={(e) => {
          const hint = document.createElement('p')
          hint.className = 'text-sm text-muted-foreground'
          hint.textContent = '浏览器无法播放该格式。'
          e.currentTarget.replaceWith(hint)
        }}
      />
    )
  }
  return <p className="text-sm text-muted-foreground">该源为直播流，不支持回放</p>
}

export function CameraLinkCard({
  camera,
  children,
}: {
  camera: Camera
  children?: ReactNode
}) {
  return (
    <Link
      to={`/cameras/${camera.id}`}
      className="block rounded-xl border bg-card p-4 transition-colors hover:bg-muted/40"
    >
      <h3 className="font-medium">
        {camera.name}{' '}
        <span className="ml-1 rounded-md bg-muted px-1.5 py-0.5 text-xs">{camera.status}</span>
      </h3>
      <div className="mt-1 font-mono text-xs text-muted-foreground">
        {camera.source_type} · {camera.source_uri}
      </div>
      {children}
    </Link>
  )
}
