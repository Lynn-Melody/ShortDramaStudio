import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Background, type Edge, Handle, type Node, type NodeProps, Position, ReactFlow, ViewportPortal,
  type Viewport as FlowViewport,
} from '@xyflow/react'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { afterRetry } from './actions'
import { api, ApiError, type ProjectSummary, type TaskNode } from './api'
import { usePollInterval } from './hooks'
import {
  blockedMessage, centreOn, detailMaxHeight, detailSide, fitViewport, formatTime, layoutTree, type PositionedNode, STATE_LABELS,
  type Viewport, viewKey,
} from './model'

type DotData = { task: PositionedNode }
type Level = { rootTaskId: string; name: string }

const stateClass = (state: string) => state.replace('_', '-')

function Dot({ data, selected }: NodeProps<Node<DotData>>) {
  const { task } = data
  return (
    <div className={`dot ${stateClass(task.state)}${selected ? ' selected' : ''}`}>
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <span className="dot-tip" role="tooltip">{task.name}</span>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  )
}

const nodeTypes = { dot: Dot }
const DOT_SIZE = 18
const MIN_ZOOM = 0.2
const MAX_ZOOM = 2

/** Saved canvas viewports per project and drill-down path, kept for the page lifetime. */
const savedViews = new Map<string, Viewport>()

function isVisible(node: { x: number; y: number }, view: Viewport, size: { width: number; height: number }) {
  const x = view.x + node.x * view.zoom
  const y = view.y + node.y * view.zoom
  return x > 24 && y > 24 && x < size.width - 24 && y < size.height - 24
}

export function TaskTree({ project, onShowOutputs }: {
  project: ProjectSummary
  onShowOutputs: (task: { id: string; name: string }) => void
}) {
  const interval = usePollInterval()
  const [levels, setLevels] = useState<Level[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [view, setView] = useState<Viewport | null>(null)
  const [cycle, setCycle] = useState<Record<string, number>>({})
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  const current = levels.at(-1)?.rootTaskId ?? null
  const key = viewKey(project.id, levels.map(level => level.rootTaskId))

  // Focus this task once its level has rendered. Drill-down, return and closing details only move focus, so a
  // restored viewport keeps the user's pan and zoom. Locate also pans, but only when the target is off screen:
  // the user asked to see that task.
  const [focusTarget, setFocusTarget] = useState<{ id: string; centre: boolean } | null>(null)

  useLayoutEffect(() => {
    const element = box.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const tree = useQuery({
    queryKey: ['task-tree', project.id, current],
    queryFn: () => api.taskTree(project.id, current),
    refetchInterval: interval,
  })
  const layout = useMemo(() => layoutTree(tree.data?.nodes ?? [], tree.data?.rootTaskId ?? null), [tree.data])
  const positions = useMemo(() => new Map(layout.nodes.map(node => [node.id, node])), [layout])

  // Restore the level's saved viewport, or fit when it is first shown. Polling keeps the user's view.
  // Only data for the level being shown may fit or restore, so a previous level's tree cannot leak in.
  const levelData = tree.data && (current === null ? tree.data.view === 'running' : tree.data.rootTaskId === current)
  useEffect(() => {
    if (!levelData || !size.width) return
    setView(existing => existing ?? savedViews.get(key) ?? fitViewport(layout, size.width, size.height))
  }, [levelData, key, layout, size])

  useEffect(() => {
    if (!focusTarget || !view) return
    const target = positions.get(focusTarget.id)
    if (!target) return
    setFocusTarget(null)
    if (focusTarget.centre && !isVisible(target, view, size)) setView(centreOn(target, view, size.width, size.height))
    requestAnimationFrame(() => {
      box.current?.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(target.id)}"]`)
        ?.focus({ preventScroll: true })
    })
  }, [focusTarget, positions, view, size])

  // Leaving the tree (another project selected) keeps the latest viewport of the level shown.
  const latest = useRef({ key, view })
  latest.current = { key, view }
  useEffect(() => () => {
    if (latest.current.view) savedViews.set(latest.current.key, latest.current.view)
  }, [])

  // Leaving a level saves its viewport under the level's own key.
  const changeLevel = (next: Level[], focus: string | null, centre = false) => {
    if (view) savedViews.set(key, view)
    setLevels(next)
    setSelected(null)
    setView(null)
    setFocusTarget(focus ? { id: focus, centre } : null)
  }
  const enter = (task: { id: string; name: string }) => {
    if (task.id === current) return
    changeLevel([...levels, { rootTaskId: task.id, name: task.name }], task.id)
  }
  const back = () => changeLevel(levels.slice(0, -1), current)
  const fit = () => setView(fitViewport(layout, size.width, size.height))
  // Zoom around the canvas centre, like the prototype's buttons.
  const zoomBy = (factor: number) => setView(current => {
    if (!current) return current
    const zoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, current.zoom * factor))
    const ratio = zoom / current.zoom
    const cx = size.width / 2
    const cy = size.height / 2
    return { zoom, x: cx - (cx - current.x) * ratio, y: cy - (cy - current.y) * ratio }
  })

  // Fixed dimensions keep React Flow from hiding nodes while it re-measures controlled updates.
  const nodes = useMemo<Node<DotData>[]>(() => layout.nodes.map(task => ({
    id: task.id,
    type: 'dot',
    position: { x: task.x, y: task.y },
    origin: [0.5, 0.5],
    width: DOT_SIZE,
    height: DOT_SIZE,
    data: { task },
    selected: task.id === selected,
    draggable: false,
    connectable: false,
    ariaLabel: `${task.name}，${STATE_LABELS[task.state]}`,
    domAttributes: { 'data-state': task.state } as Node['domAttributes'],
  })), [layout, selected])
  const edges = useMemo<Edge[]>(() => layout.nodes
    .filter(task => task.parentId && positions.has(task.parentId))
    .map(task => ({ id: `${task.parentId}-${task.id}`, source: task.parentId!, target: task.id, type: 'smoothstep',
      focusable: false, selectable: false })), [layout, positions])

  const locate = async (state: 'running' | 'timed_out') => {
    const visible = layout.nodes.filter(task => task.state === state)
    if (visible.length) {
      const index = ((cycle[state] ?? -1) + 1) % visible.length
      setCycle({ ...cycle, [state]: index })
      setSelected(visible[index].id)
      // Locate only pans when the target is off screen; a visible target keeps the user's viewport.
      setFocusTarget({ id: visible[index].id, centre: true })
      return
    }
    const found = await api.tasks(project.id, { state, limit: 1 })
    const target = found.results[0]
    if (!target) return
    // Open the subtree rooted at the task's parent so the task itself is visible.
    const parent = target.path.at(-1)
    if (!parent) return
    const parentNode = await api.task(project.id, parent)
    changeLevel([...levels, { rootTaskId: parent, name: parentNode.name }], target.id, true)
    setSelected(target.id)
  }

  const root = levels.length ? positions.get(current!) : undefined
  const selectedNode = selected ? positions.get(selected) : undefined
  const hasRunning = layout.nodes.some(task => task.state === 'running')
  const title = levels.at(-1)?.name ?? '运行路径'

  return (
    <section className="graph pane" aria-label="任务图">
      <div className="pane-heading graph-heading">
        <div>
          <h2>{title}</h2>
          <span>{layout.nodes.length} 个节点</span>
        </div>
        <div className="quick-actions">
          <button type="button" disabled={!hasRunning} onClick={() => locate('running')}>定位运行</button>
          <button type="button" disabled={!project.timedOutCount} onClick={() => locate('timed_out')}>定位超时</button>
        </div>
      </div>
      <div ref={box} className="viewport" aria-label="任务画布">
        <span className="graph-hint">双击节点查看子任务</span>
        {tree.isError && <p className="canvas-error" role="alert">任务树加载失败：{(tree.error as Error).message}</p>}
        {view && (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            viewport={view}
            onViewportChange={(next: FlowViewport) => setView(next)}
            autoPanOnNodeFocus={false}
            minZoom={MIN_ZOOM}
            maxZoom={MAX_ZOOM}
            zoomOnDoubleClick={false}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_, node) => setSelected(node.id)}
            onNodeDoubleClick={(_, node) => enter(node.data.task)}
            onNodesChange={() => {}}
            onPaneClick={() => setSelected(null)}
            onKeyDown={event => {
              const id = (event.target as HTMLElement).closest('[data-id]')?.getAttribute('data-id')
              const task = id ? positions.get(id) : undefined
              if (!task) return
              if (event.key === 'Enter' || event.key === ' ') setSelected(task.id)
              if (event.key === 'ArrowRight') {
                event.preventDefault()
                enter(task)
              }
            }}
          >
            <Background gap={18} size={1} />
            {root && (
              <ViewportPortal>
                <button
                  type="button"
                  className="tree-back"
                  aria-label="返回上一层"
                  style={{ transform: `translate(${root.x - 14}px, ${root.y - 44}px)` }}
                  onClick={back}
                >返回</button>
              </ViewportPortal>
            )}
          </ReactFlow>
        )}
        {selectedNode && (
          <TaskDetailCard
            projectId={project.id}
            taskId={selectedNode.id}
            side={view ? detailSide(selectedNode, view, size.height) : 'bottom'}
            maxHeight={detailMaxHeight(size.height)}
            onClose={() => {
              setSelected(null)
              setFocusTarget({ id: selectedNode.id, centre: false })
            }}
            onOpenSubtree={() => enter(selectedNode)}
            onShowOutputs={onShowOutputs}
          />
        )}
        <div className="view-tools">
          <button type="button" aria-label="缩小" onClick={() => zoomBy(1 / 1.2)}>−</button>
          <span>{view ? Math.round(view.zoom * 100) : 100}%</span>
          <button type="button" aria-label="放大" onClick={() => zoomBy(1.2)}>＋</button>
          <button type="button" onClick={fit}>全图</button>
        </div>
      </div>
      <div className="legend" aria-label="任务状态">
        <span className="completed">完成</span>
        <span className="running">运行</span>
        <span className="timed-out">超时</span>
        <span className="waiting">等待</span>
      </div>
    </section>
  )
}

function TaskDetailCard({ projectId, taskId, side, maxHeight, onClose, onOpenSubtree, onShowOutputs }: {
  projectId: string
  taskId: string
  side: 'top' | 'bottom'
  maxHeight: number
  onClose: () => void
  onOpenSubtree: () => void
  onShowOutputs: (task: { id: string; name: string }) => void
}) {
  const client = useQueryClient()
  const interval = usePollInterval()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const detail = useQuery({
    queryKey: ['task', projectId, taskId],
    queryFn: () => api.task(projectId, taskId),
    refetchInterval: interval,
  })
  useEffect(() => setError(null), [taskId])
  const task = detail.data

  const retry = async () => {
    setBusy(true)
    setError(null)
    try {
      await afterRetry(client, projectId, await api.retry(projectId, taskId))
    } catch (exc) {
      setError(exc instanceof ApiError && exc.code === 'provider_check_pending'
        ? '待核查供应商任务，暂不能重试。'
        : `重试失败：${(exc as Error).message}`)
      await client.invalidateQueries({ queryKey: ['task', projectId, taskId] })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div
      className="task-detail"
      data-side={side}
      style={{ maxHeight }}
      role="dialog"
      aria-label="任务详情"
      onDoubleClick={event => event.stopPropagation()}
    >
      <div className="detail-heading">
        <strong>{task?.name ?? '任务详情'}</strong>
        <button type="button" aria-label="关闭任务详情" onClick={onClose}>×</button>
      </div>
      {detail.isError && <p role="alert">读取失败：{(detail.error as Error).message}</p>}
      {task && (
        <>
          <span className={`state ${stateClass(task.state)}`}>{STATE_LABELS[task.state]}</span>
          <TaskMessage task={task} />
          {task.childTotal > 0 && (
            <p>子任务 {task.childCount}/{task.childTotal} · 进度 {Math.floor(task.progress * 100)}%</p>
          )}
          {task.currentExecution && (
            <p className="execution">
              当前执行 {task.currentExecution.id.slice(0, 8)} · 开始于 {formatTime(task.currentExecution.startedAt)}
            </p>
          )}
          <div className="detail-actions">
            {task.canRetry && <button type="button" disabled={busy} onClick={retry}>重试</button>}
            {task.providerCheckPending && <span className="badge timed-out">待核查供应商任务</span>}
            {task.childTotal > 0 && <button type="button" onClick={onOpenSubtree}>查看子任务</button>}
            {task.outputCount > 0 && (
              <button type="button" className="primary" onClick={() => onShowOutputs({ id: task.id, name: task.name })}>
                查看 {task.outputCount} 个产出
              </button>
            )}
          </div>
          {error && <p role="alert" className="error">{error}</p>}
        </>
      )}
    </div>
  )
}

function TaskMessage({ task }: { task: import('./api').TaskDetail }) {
  const blocked = blockedMessage(task.blockedReason)
  if (task.state === 'timed_out') {
    return <p>{task.providerCheckPending ? blockedMessage(task.blockedReason) : '执行超过时限，自动重试已用尽。'}</p>
  }
  if (task.state === 'running') return <p>正在处理此任务。</p>
  if (task.state === 'completed') return <p>{task.completionReason || '此任务已完成。'}</p>
  if (blocked) {
    const failed = task.executions.find(execution => execution.errorCode)
    return <p>{blocked}{failed?.message ? ` ${failed.message}` : ''}</p>
  }
  const pendingDependency = task.dependencies.find(dependency => !dependency.satisfied)
  const pendingWait = task.waits.find(wait => !wait.satisfied)
  if (pendingDependency) return <p>等待 {pendingDependency.task.name} 完成。</p>
  if (pendingWait) return <p>等待子任务 {pendingWait.task?.name ?? pendingWait.slotKey} 完成。</p>
  if (task.retryAt) return <p>将于 {formatTime(task.retryAt)} 自动重试。</p>
  return <p>等待派发。</p>
}

export type { TaskNode }
