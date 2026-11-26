import { afterEach, expect, test, vi } from 'vitest'
import { fetchHealth } from './health'

afterEach(() => vi.unstubAllGlobals())

test('reads the API through the same-origin proxy', async () => {
  const fetchMock = vi.fn().mockResolvedValue(Response.json({ status: 'ok' }))
  vi.stubGlobal('fetch', fetchMock)
  await expect(fetchHealth()).resolves.toEqual({ status: 'ok' })
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/health')
})

test('reports an HTTP error', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })))
  await expect(fetchHealth()).rejects.toThrow('503')
})
