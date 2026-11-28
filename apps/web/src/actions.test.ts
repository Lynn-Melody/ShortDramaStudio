import { QueryClient } from '@tanstack/react-query'
import { expect, test } from 'vitest'
import { afterLogout, afterRetry, afterSwitch, keys } from './actions'
import type { ProjectDetail, TaskDetail } from './api'

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

test('a poll that started before retry cannot bring back the retry button', async () => {
  const client = new QueryClient()
  const key = keys.task('p', 't')
  const stale = deferred<TaskDetail>()
  const fresh = { id: 't', state: 'waiting', canRetry: false } as TaskDetail
  let calls = 0
  const observer = client.fetchQuery({ queryKey: key, queryFn: () => (calls++ ? Promise.resolve(fresh) : stale.promise) })
  const retried = afterRetry(client, 'p', fresh)
  stale.resolve({ id: 't', state: 'waiting', canRetry: true } as TaskDetail)
  await Promise.allSettled([observer, retried])
  expect(client.getQueryData<TaskDetail>(key)?.canRetry).toBe(false)
})

test('pause or resume keeps its summary over an older project poll', async () => {
  const client = new QueryClient()
  const key = keys.project('p')
  client.setQueryData(key, { id: 'p', status: 'running', name: 'n' } as ProjectDetail)
  const stale = deferred<ProjectDetail>()
  const poll = client.fetchQuery({ queryKey: key, queryFn: () => stale.promise }).catch(() => undefined)
  const switched = afterSwitch(client, { id: 'p', status: 'paused' } as ProjectDetail)
  stale.resolve({ id: 'p', status: 'running', name: 'n' } as ProjectDetail)
  await Promise.allSettled([poll, switched])
  expect(client.getQueryData<ProjectDetail>(key)?.status).toBe('paused')
})

test('sign-out drops workbench data and installs the anonymous session', () => {
  const client = new QueryClient()
  client.setQueryData(keys.projects, { pages: [] })
  client.setQueryData(keys.session, { authenticated: true })
  afterLogout(client, { authenticated: false, username: null, csrfToken: 'new' })
  expect(client.getQueryData(keys.projects)).toBeUndefined()
  expect(client.getQueryData(keys.session)).toEqual({ authenticated: false, username: null, csrfToken: 'new' })
})

test('previews re-sign shortly before their signature expires', async () => {
  const { refreshDelay } = await import('./AssetLibrary')
  const now = Date.parse('2026-11-28T12:00:00Z')
  expect(refreshDelay(undefined, now)).toBe(false)
  expect(refreshDelay('2026-11-28T12:05:00Z', now)).toBe(270_000)
  expect(refreshDelay('2026-11-28T12:00:10Z', now)).toBe(5_000)
})
