import { afterEach, expect, test, vi } from 'vitest'
import { api, ApiError, rememberCsrf, withQuery } from './api'
import type { TaskNode } from './api'
import {
  blockedMessage, detailSide, dotVisible, fitViewport, layoutTree, percent, previewKind, projectControl, projectNotes, scopeQuery,
  storedTheme,
} from './model'

afterEach(() => vi.unstubAllGlobals())

function node(id: string, parentId: string | null, state: TaskNode['state'] = 'waiting'): TaskNode {
  return {
    id, parentId, name: id, taskType: 'short_drama', state, slotKey: null, blockedReason: null, canRetry: false,
    providerCheckPending: false, progress: 0, childTotal: 0, childCount: 0, episodeNumber: null, shotNumber: null,
  }
}

test('progress shows 100% only when reached', () => {
  expect(percent(0)).toBe(0)
  expect(percent(1 / 3)).toBe(33)
  expect(percent(0.9999)).toBe(99)
  expect(percent(1)).toBe(100)
})

test('project control follows status and start time', () => {
  expect(projectControl({ status: 'paused', startedAt: null })).toEqual({ label: '▶ 启动制作', action: 'resume' })
  expect(projectControl({ status: 'paused', startedAt: '2026-11-28T00:00:00Z' })?.label).toBe('▶ 继续')
  expect(projectControl({ status: 'running', startedAt: '2026-11-28T00:00:00Z' })?.action).toBe('pause')
  expect(projectControl({ status: 'completed', startedAt: '2026-11-28T00:00:00Z' })).toBeNull()
})

test('project notes mention wind-down, timeouts and failures', () => {
  const notes = projectNotes({
    status: 'paused', runningCount: 2, timedOutCount: 1, blockedCount: 1,
    currentEpisode: { id: 1, number: 3, title: 'x' },
  } as never)
  expect(notes).toEqual(['当前第 3 集', '2 个运行中的任务正在收尾', '1 个任务超时', '1 个任务失败待处理'])
})

test('shared ancestors appear once and parents centre over their children', () => {
  const layout = layoutTree([
    node('root', null), node('episodes', 'root'), node('a', 'episodes', 'running'), node('b', 'episodes', 'running'),
  ], 'root')
  expect(layout.nodes.map(item => item.id)).toEqual(['root', 'episodes', 'a', 'b'])
  const [root, episodes, a, b] = layout.nodes
  expect(episodes.x).toBe((a.x + b.x) / 2)
  expect(root.x).toBe(episodes.x)
  expect([root.depth, episodes.depth, a.depth]).toEqual([0, 1, 2])
})

test('a subtree is laid out from its own root even when ancestors are absent', () => {
  const layout = layoutTree([node('mid', 'root'), node('leaf', 'mid')], 'mid')
  expect(layout.rootId).toBe('mid')
  expect(layout.nodes.map(item => item.depth)).toEqual([0, 1])
})

test('fit and detail placement keep the selected dot uncovered', () => {
  const view = fitViewport({ width: 200, height: 100 }, 600, 400)
  expect(view.zoom).toBeGreaterThan(0)
  expect(detailSide({ y: 0 }, { x: 0, y: 0, zoom: 1 }, 400)).toBe('bottom')
  expect(detailSide({ y: 300 }, { x: 0, y: 0, zoom: 1 }, 400)).toBe('top')
})

test('locate leaves fully visible dots where they are', () => {
  const size = { width: 400, height: 300 }
  const view = { x: 0, y: 0, zoom: 1 }
  // An 18 px dot centred 20 px from the left edge spans 11-29 px: fully visible, so no pan.
  expect(dotVisible({ x: 20, y: 150 }, view, size, 18)).toBe(true)
  expect(dotVisible({ x: 9, y: 150 }, view, size, 18)).toBe(true)
  expect(dotVisible({ x: 8, y: 150 }, view, size, 18)).toBe(false)
  expect(dotVisible({ x: 395, y: 150 }, view, size, 18)).toBe(false)
  expect(dotVisible({ x: 20, y: 75 }, { x: 0, y: 0, zoom: 2 }, size, 18)).toBe(true)
  expect(dotVisible({ x: 20, y: 150 }, { x: 0, y: 0, zoom: 2 }, size, 18)).toBe(false)
  expect(dotVisible({ x: 4, y: 75 }, { x: 0, y: 0, zoom: 2 }, size, 18)).toBe(false)
})

test('blocked reasons explain provider checks and failures', () => {
  expect(blockedMessage(null)).toBeNull()
  expect(blockedMessage('provider_jobs_unverified')).toContain('待核查供应商任务')
  expect(blockedMessage('script_rejected')).toContain('script_rejected')
})

test('library scope and preview kinds', () => {
  expect(scopeQuery('episode:7')).toEqual({ scope: 'episode', episodeId: 7 })
  expect(scopeQuery('shared')).toEqual({ scope: 'shared' })
  expect(previewKind('image/png')).toBe('image')
  expect(previewKind('image/svg+xml')).toBe('file')
  expect(previewKind('video/mp4')).toBe('video')
  expect(previewKind('text/vtt')).toBe('file')
})

test('theme defaults to light and survives storage failures', () => {
  expect(storedTheme({ getItem: () => 'dark' })).toBe('dark')
  expect(storedTheme({ getItem: () => null })).toBe('light')
  expect(storedTheme({ getItem: () => { throw new Error('denied') } })).toBe('light')
})

test('requests send the CSRF token and surface field and conflict errors', async () => {
  rememberCsrf({ authenticated: true, username: 'director', csrfToken: 'token-1' })
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(Response.json({ idea: ['This field may not be blank.'] }, { status: 400 }))
    .mockResolvedValueOnce(Response.json({ detail: 'busy', code: 'provider_check_pending' }, { status: 409 }))
  vi.stubGlobal('fetch', fetchMock)
  const created = await api.createProject(' ', 'id-1').catch(error => error)
  expect(created).toBeInstanceOf(ApiError)
  expect(created.fields).toEqual({ idea: ['This field may not be blank.'] })
  const [, init] = fetchMock.mock.calls[0]
  expect(init.headers['X-CSRFToken']).toBe('token-1')
  expect(JSON.parse(init.body)).toEqual({ idea: ' ', projectId: 'id-1' })
  const retried = await api.retry('p', 't').catch(error => error)
  expect([retried.status, retried.code]).toEqual([409, 'provider_check_pending'])
})

test('queries omit empty parameters', () => {
  expect(withQuery('/x', { scope: 'all', q: '', shotId: undefined, limit: 2 })).toBe('/x?scope=all&limit=2')
})
