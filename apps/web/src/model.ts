import type { AssetKindFilter, ProjectSummary, TaskNode, TaskState } from './api'

export const STATE_LABELS: Record<TaskState, string> = {
  waiting: '等待',
  running: '运行',
  timed_out: '超时',
  completed: '完成',
}

export const PROJECT_STATUS: Record<ProjectSummary['status'], { label: string; tone: TaskState }> = {
  running: { label: '运行中', tone: 'running' },
  paused: { label: '已暂停', tone: 'waiting' },
  completed: { label: '已完成', tone: 'completed' },
}

/** Display the structural progress as a percentage, truncating so 100% only appears when it is reached. */
export function percent(progress: number) {
  return Math.min(100, Math.floor(progress * 100 + 1e-9))
}

export type ProjectControl = { label: string; action: 'resume' | 'pause' } | null

export function projectControl(project: Pick<ProjectSummary, 'status' | 'startedAt'>): ProjectControl {
  if (project.status === 'completed') return null
  if (project.status === 'running') return { label: 'Ⅱ 暂停', action: 'pause' }
  return { label: project.startedAt ? '▶ 继续' : '▶ 启动制作', action: 'resume' }
}

export function projectNotes(project: ProjectSummary) {
  const notes: string[] = []
  if (project.currentEpisode) notes.push(`当前第 ${project.currentEpisode.number} 集`)
  if (project.status === 'paused' && project.runningCount) notes.push(`${project.runningCount} 个运行中的任务正在收尾`)
  if (project.timedOutCount) notes.push(`${project.timedOutCount} 个任务超时`)
  if (project.blockedCount) notes.push(`${project.blockedCount} 个任务失败待处理`)
  return notes
}

// Task tree layout, following the prototype: parents centred over their children, one row per depth.

export const NODE_SPACING = 69
export const ROW_SPACING = 69

export type PositionedNode = TaskNode & { x: number; y: number; depth: number }
export type GraphLayout = { nodes: PositionedNode[]; width: number; height: number; rootId: string | null }

export function layoutTree(nodes: TaskNode[], rootId: string | null): GraphLayout {
  const byId = new Map(nodes.map(node => [node.id, node]))
  const root = rootId && byId.has(rootId) ? byId.get(rootId)! : nodes.find(node => !node.parentId || !byId.has(node.parentId))
  if (!root) return { nodes: [], width: 0, height: 0, rootId: null }
  const children = new Map<string, TaskNode[]>()
  for (const node of nodes) {
    if (node.parentId && byId.has(node.parentId) && node.id !== root.id) {
      children.set(node.parentId, [...(children.get(node.parentId) ?? []), node])
    }
  }
  const positioned: PositionedNode[] = []
  let leaf = 0
  const place = (node: TaskNode, depth: number): number => {
    const entry: PositionedNode = { ...node, x: 0, y: depth * ROW_SPACING, depth }
    positioned.push(entry)
    const centres = (children.get(node.id) ?? []).map(child => place(child, depth + 1))
    entry.x = centres.length ? (centres[0] + centres[centres.length - 1]) / 2 : leaf++ * NODE_SPACING
    return entry.x
  }
  place(root, 0)
  return {
    nodes: positioned,
    width: Math.max(0, leaf - 1) * NODE_SPACING,
    height: Math.max(...positioned.map(node => node.y)),
    rootId: root.id,
  }
}

export type Viewport = { x: number; y: number; zoom: number }
const PADDING = 56

export function fitViewport(layout: Pick<GraphLayout, 'width' | 'height'>, width: number, height: number): Viewport {
  const zoom = Math.min(1.55, Math.max(0.2, Math.min(
    (width - PADDING * 2) / Math.max(layout.width, 1),
    (height - PADDING * 2) / Math.max(layout.height, 1),
  )))
  return { zoom, x: (width - layout.width * zoom) / 2, y: (height - layout.height * zoom) / 2 }
}

export function centreOn(node: { x: number; y: number }, view: Viewport, width: number, height: number): Viewport {
  const zoom = Math.max(view.zoom, 0.87)
  return { zoom, x: width / 2 - node.x * zoom, y: height / 2 - node.y * zoom }
}

/** Keep the detail card on the half of the canvas away from the selected dot. */
export function detailSide(node: { y: number } | undefined, view: Viewport, height: number): 'top' | 'bottom' {
  return node && view.y + node.y * view.zoom > height / 2 ? 'top' : 'bottom'
}

/** The card is limited to the half of the canvas opposite the dot, so it never covers it. */
export const DETAIL_MARGIN = 12

export function detailMaxHeight(height: number) {
  return Math.max(0, height / 2 - DETAIL_MARGIN * 2)
}

export function viewKey(projectId: string, path: string[]) {
  return [projectId, ...path].join('/')
}

// Task detail wording

export function blockedMessage(reason: string | null | undefined) {
  if (!reason) return null
  if (reason === 'provider_jobs_unverified') return '待核查供应商任务：确认工作流和供应商作业已停止后才能重试。'
  if (reason === 'invalid_workflow_result') return '工作流结果未通过校验，需要人工处理后重试。'
  return `执行失败（${reason}），已停止自动重试。`
}

// Asset library

export const ASSET_TABS: { label: string; kind: AssetKindFilter | 'documents' | null }[] = [
  { label: '全部', kind: null },
  { label: '剧本', kind: 'documents' },
  { label: '角色、场景与道具', kind: 'visual' },
  { label: '分镜', kind: 'image' },
  { label: '音频', kind: 'audio' },
  { label: '视频', kind: 'video' },
  { label: '交付', kind: 'delivery' },
]

export const KIND_LABELS: Record<string, string> = {
  character: '人物', scene: '场景', prop: '道具', image: '分镜', audio: '音频', video: '视频',
  subtitles: '字幕', timeline: '时间轴',
}

/** Library scope selector value: current, shared, all, or episode:<id>. */
export type ScopeChoice = 'current' | 'shared' | 'all' | `episode:${number}`

export function scopeQuery(choice: ScopeChoice) {
  if (choice.startsWith('episode:')) return { scope: 'episode', episodeId: Number(choice.slice(8)) }
  return { scope: choice }
}

export type LibraryFilters = { scope: ScopeChoice; tab: number; shotId: number | null; q: string }
export const DEFAULT_FILTERS: LibraryFilters = { scope: 'current', tab: 0, shotId: null, q: '' }

export type PreviewKind = 'image' | 'video' | 'audio' | 'file'

export function previewKind(contentType: string): PreviewKind {
  const type = contentType.toLowerCase()
  if (['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(type)) return 'image'
  if (type.startsWith('video/')) return 'video'
  if (type.startsWith('audio/')) return 'audio'
  return 'file'
}

export function formatBytes(size: number) {
  if (size < 1024) return `${size} B`
  if (size < 1024 ** 2) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 ** 2).toFixed(1)} MB`
}

export function formatTime(value: string | null | undefined) {
  if (!value) return ''
  return new Date(value).toLocaleString('zh-CN', { hour12: false })
}

// Theme

export type Theme = 'light' | 'dark'
export const THEME_KEY = 'studio-theme'

export function storedTheme(storage: Pick<Storage, 'getItem'> | undefined): Theme {
  try {
    return storage?.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}
