import { describe, expect, it, vi, afterEach } from 'vitest'
import { api, ApiError } from './api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('api', () => {
  it('把 404 的 detail 字符串放进 ApiError.message', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 404,
        json: async () => ({ detail: '摄像头不存在' }),
      }),
    )
    await expect(api('/cameras/9')).rejects.toMatchObject({
      message: '摄像头不存在',
      status: 404,
    })
    await expect(api('/cameras/9')).rejects.toBeInstanceOf(ApiError)
  })

  it('把 FastAPI 校验数组拼成可读消息', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({
          detail: [{ loc: ['body', 'name'], msg: 'Field required', type: 'missing' }],
        }),
      }),
    )
    await expect(api('/cameras', { method: 'POST' })).rejects.toMatchObject({
      message: 'Field required',
    })
  })

  it('204 返回 null', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 204,
      }),
    )
    await expect(api('/cameras/1')).resolves.toBeNull()
  })
})
