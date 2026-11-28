import type { components } from './api.d'

type Schemas = components['schemas']
export type Session = Schemas['Session']
export type ProjectSummary = Schemas['ProjectSummary']
export type ProjectDetail = Schemas['ProjectDetail']
export type ProjectList = Schemas['ProjectList']
export type EpisodeList = Schemas['EpisodeList']
export type EpisodeSummary = Schemas['EpisodeSummary']
export type EpisodeDetail = Schemas['EpisodeDetail']
export type ShotList = Schemas['ShotList']
export type TaskTree = Schemas['TaskTree']
export type TaskNode = Schemas['TaskNode']
export type TaskList = Schemas['TaskList']
export type TaskDetail = Schemas['TaskDetail']
export type TaskState = Schemas['TaskStateEnum']
export type TaskOutputs = Schemas['TaskOutputs']
export type AssetList = Schemas['AssetList']
export type AssetCard = Schemas['AssetCard']
export type AssetVersion = Schemas['AssetVersion']
export type AssetVersionList = Schemas['AssetVersionList']
export type AssetPreview = Schemas['AssetPreview']
export type DeliveryList = Schemas['DeliveryList']
export type AssetKindFilter = NonNullable<
  NonNullable<import('./api.d').operations['assets_list']['parameters']['query']>['kind']
>

/** A failed API call. fields holds 400 messages; code holds the 409 conflict code. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly fields: Record<string, string[]> = {},
    readonly code?: string,
  ) {
    super(message)
  }
}

let csrfToken = ''

export function rememberCsrf(session: Session) {
  csrfToken = session.csrfToken
}

function describe(status: number, body: unknown): ApiError {
  if (body && typeof body === 'object') {
    const record = body as Record<string, unknown>
    if (typeof record.detail === 'string') {
      return new ApiError(status, record.detail, {}, typeof record.code === 'string' ? record.code : undefined)
    }
    const fields: Record<string, string[]> = {}
    for (const [key, value] of Object.entries(record)) {
      if (Array.isArray(value)) fields[key] = value.map(String)
    }
    const first = Object.values(fields)[0]?.[0]
    if (first) return new ApiError(status, first, fields)
  }
  return new ApiError(status, `请求失败（${status}）`)
}

type Query = Record<string, string | number | boolean | undefined | null>

export function withQuery(path: string, query: Query = {}) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `${path}?${text}` : path
}

export async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (method !== 'GET') headers['X-CSRFToken'] = csrfToken
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const response = await fetch(path, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 204) return undefined as T
  const data: unknown = await response.json().catch(() => null)
  if (!response.ok) throw describe(response.status, data)
  return data as T
}

const project = (id: string) => `/api/v1/projects/${encodeURIComponent(id)}`

export const api = {
  session: () => request<Session>('GET', '/api/v1/session'),
  login: (username: string, password: string) =>
    request<Session>('POST', '/api/v1/session', { username, password }),
  logout: () => request<void>('DELETE', '/api/v1/session'),
  projects: (cursor?: string) => request<ProjectList>('GET', withQuery('/api/v1/projects', { cursor })),
  createProject: (idea: string, projectId: string) =>
    request<ProjectDetail>('POST', '/api/v1/projects', { idea, projectId }),
  project: (id: string) => request<ProjectDetail>('GET', project(id)),
  pause: (id: string) => request<ProjectSummary>('POST', `${project(id)}/pause`),
  resume: (id: string) => request<ProjectSummary>('POST', `${project(id)}/resume`),
  episodes: (id: string) => request<EpisodeList>('GET', `${project(id)}/episodes`),
  episode: (id: string, episodeId: number) => request<EpisodeDetail>('GET', `${project(id)}/episodes/${episodeId}`),
  shots: (id: string, episodeId: number) => request<ShotList>('GET', `${project(id)}/episodes/${episodeId}/shots`),
  taskTree: (id: string, rootTaskId: string | null) =>
    request<TaskTree>('GET', withQuery(`${project(id)}/task-tree`, rootTaskId
      ? { view: 'subtree', rootTaskId }
      : { view: 'running' })),
  tasks: (id: string, query: { state?: TaskState; blocked?: boolean; cursor?: string; limit?: number }) =>
    request<TaskList>('GET', withQuery(`${project(id)}/tasks`, query)),
  task: (id: string, taskId: string) => request<TaskDetail>('GET', `${project(id)}/tasks/${taskId}`),
  retry: (id: string, taskId: string) => request<TaskDetail>('POST', `${project(id)}/tasks/${taskId}/retry`),
  taskOutputs: (id: string, taskId: string) => request<TaskOutputs>('GET', `${project(id)}/tasks/${taskId}/outputs`),
  assets: (id: string, query: Query) => request<AssetList>('GET', withQuery(`${project(id)}/assets`, query)),
  assetVersions: (id: string, assetId: string) =>
    request<AssetVersionList>('GET', `${project(id)}/assets/${assetId}/versions`),
  deliveries: (id: string, episodeId?: number) =>
    request<DeliveryList>('GET', withQuery(`${project(id)}/deliveries`, { episodeId })),
  preview: (id: string, versionId: string) =>
    request<AssetPreview>('GET', `${project(id)}/asset-versions/${versionId}/preview`),
}
