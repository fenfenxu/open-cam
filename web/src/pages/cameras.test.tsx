import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { VideoTile } from '@/components/app/video-wall'
import { CamerasPage } from '@/pages/cameras'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

describe('摄像头文案', () => {
  it('RTSP 显示不支持回放', () => {
    render(
      <VideoTile
        camera={{
          id: 1,
          name: '流',
          source_type: 'rtsp',
          source_uri: 'rtsp://x',
          status: 'stopped',
        }}
      />,
    )
    expect(screen.getByText('该源为直播流，不支持回放')).toBeInTheDocument()
  })

  it('列表页含请新建约束', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <CamerasPage />
        </MemoryRouter>
      </QueryClientProvider>,
    )
    expect(screen.getByText(/请新建/)).toBeInTheDocument()
  })
})
