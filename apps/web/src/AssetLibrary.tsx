import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  api, type AssetCard, type AssetVersion, type DeliveryList, type ProjectDetail, type TaskOutputs,
} from './api'
import { usePollInterval } from './hooks'
import {
  ASSET_TABS, DEFAULT_FILTERS, formatBytes, formatTime, KIND_LABELS, type LibraryFilters, previewKind,
  type ScopeChoice, scopeQuery,
} from './model'

type OutputView = { id: string; name: string }

export function AssetLibrary({ project, output, onCloseOutput }: {
  project: ProjectDetail
  output: OutputView | null
  onCloseOutput: () => void
}) {
  const [filters, setFilters] = useState<LibraryFilters>(DEFAULT_FILTERS)
  const [expanded, setExpanded] = useState(false)
  const [preview, setPreview] = useState<{ version: AssetVersion; title: string } | null>(null)
  const list = useRef<HTMLDivElement>(null)
  const outputList = useRef<HTMLDivElement>(null)
  // The library scroll is recorded while it is visible, so the output view cannot overwrite it.
  const savedScroll = useRef(0)
  const dialog = useRef<HTMLDialogElement>(null)

  const wasOutput = useRef(false)
  useLayoutEffect(() => {
    if (!output && wasOutput.current && list.current) list.current.scrollTop = savedScroll.current
    wasOutput.current = Boolean(output)
  })

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (expanded && !element.open) element.showModal()
    if (!expanded && element.open) element.close()
  }, [expanded])

  const panel = (
    <section className="assets pane" aria-label="素材库">
      <div className="pane-heading">
        <h2>素材库</h2>
        <div className="asset-heading-actions">
          <button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? '收起' : '展开'}</button>
        </div>
      </div>
      {output && (
        <OutputList project={project} output={output} onBack={onCloseOutput} onPreview={setPreview} listRef={outputList} />
      )}
      <div className="library-body" hidden={Boolean(output)}>
        <Library project={project} filters={filters} setFilters={setFilters} onPreview={setPreview} listRef={list}
          onScroll={top => { if (!output) savedScroll.current = top }} />
      </div>
    </section>
  )

  return (
    <>
      {!expanded && panel}
      <dialog
        ref={dialog}
        className="library-dialog"
        aria-label="展开的素材库"
        onClose={() => setExpanded(false)}
      >
        {expanded && panel}
      </dialog>
      {preview && <PreviewDialog projectId={project.id} {...preview} onClose={() => setPreview(null)} />}
    </>
  )
}

type ListProps = {
  project: ProjectDetail
  onPreview: (value: { version: AssetVersion; title: string }) => void
  listRef: React.RefObject<HTMLDivElement | null>
}

function Library({ project, filters, setFilters, onPreview, listRef, onScroll }: ListProps & {
  filters: LibraryFilters
  setFilters: (filters: LibraryFilters) => void
  onScroll: (top: number) => void
}) {
  const interval = usePollInterval()
  const tab = ASSET_TABS[filters.tab]
  const episodes = useQuery({
    queryKey: ['episodes', project.id],
    queryFn: () => api.episodes(project.id),
    refetchInterval: interval,
  })
  const scope = scopeQuery(filters.scope)
  const resolvedEpisodeId = 'episodeId' in scope ? scope.episodeId
    : filters.scope === 'current' ? project.currentEpisode?.id : undefined
  const shots = useQuery({
    queryKey: ['shots', project.id, resolvedEpisodeId],
    queryFn: () => api.shots(project.id, resolvedEpisodeId!),
    enabled: resolvedEpisodeId !== undefined,
    // Shots appear when the episode breakdown completes.
    refetchInterval: interval,
  })
  // A shot filter only applies within its episode; drop it when the current episode moves on.
  const shotEpisode = useRef(resolvedEpisodeId)
  useEffect(() => {
    if (shotEpisode.current !== resolvedEpisodeId && filters.shotId !== null) {
      setFilters({ ...filters, shotId: null })
    }
    shotEpisode.current = resolvedEpisodeId
  }, [resolvedEpisodeId, filters, setFilters])
  const shotId = filters.shotId !== null && shots.data?.results.some(shot => shot.id === filters.shotId)
    ? filters.shotId : null
  // Once the client knows the current episode, request that episode explicitly: the shot filter and the
  // cursors then belong to the same episode even if production moves on between polls.
  const pinned = filters.scope === 'current' && resolvedEpisodeId !== undefined
    ? { scope: 'episode', episodeId: resolvedEpisodeId } : scope
  const query = {
    ...pinned,
    kind: tab.kind === 'documents' ? undefined : tab.kind ?? undefined,
    shotId: shotId ?? undefined,
    q: filters.q.trim() || undefined,
    limit: 60,
  }
  const assets = useInfiniteQuery({
    queryKey: ['assets', project.id, query],
    queryFn: ({ pageParam }) => api.assets(project.id, { ...query, cursor: pageParam }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: page => page.nextCursor ?? undefined,
    refetchInterval: interval,
    enabled: tab.kind !== 'documents',
  })
  const cards = assets.data?.pages.flatMap(page => page.results) ?? []
  const resolved = assets.data?.pages[0]
  const update = (patch: Partial<LibraryFilters>) => setFilters({ ...filters, ...patch })
  const scopeLabel = resolved?.scope === 'episode' && resolved.episode ? `第 ${resolved.episode.number} 集`
    : resolved?.scope === 'shared' ? '全剧共用' : resolved?.scope === 'all' ? '全项目' : ''

  return (
    <>
      <div className="asset-controls">
        <div className="asset-types" role="group" aria-label="素材类型">
          {ASSET_TABS.map((item, index) => (
            <button key={item.label} type="button" aria-pressed={filters.tab === index}
              onClick={() => update({ tab: index })}>{item.label}</button>
          ))}
        </div>
        <input
          className="asset-search"
          type="search"
          aria-label="搜索素材名称"
          placeholder="搜索名称"
          value={filters.q}
          onChange={event => update({ q: event.target.value })}
        />
        <div className="filters">
          <select aria-label="范围" value={filters.scope}
            onChange={event => update({ scope: event.target.value as ScopeChoice, shotId: null })}>
            <option value="current">当前制作{project.currentEpisode ? ` · 第 ${project.currentEpisode.number} 集` : ' · 全剧共用'}</option>
            <option value="shared">全剧共用</option>
            <option value="all">全项目</option>
            {episodes.data?.results.map(episode => (
              <option key={episode.id} value={`episode:${episode.id}`}>第 {episode.number} 集 · {episode.title}</option>
            ))}
          </select>
          <select aria-label="镜头" value={shotId ?? ''} disabled={!shots.data}
            onChange={event => update({ shotId: event.target.value ? Number(event.target.value) : null })}>
            <option value="">全部镜头</option>
            {shots.data?.results.map(shot => <option key={shot.id} value={shot.id}>镜头 {shot.number}</option>)}
          </select>
        </div>
      </div>
      <div ref={listRef} className="asset-list" onScroll={event => onScroll(event.currentTarget.scrollTop)}>
        {tab.kind === 'documents'
          ? <Documents project={project} episodeId={resolvedEpisodeId} />
          : (
            <>
              {scopeLabel && <p className="asset-count">{scopeLabel} · {cards.length}{assets.hasNextPage ? '+' : ''} 份素材</p>}
              {/* Delivery groups follow the same scope; shared assets have none, and shot or name filters narrow
                  the cards below instead. */}
              {tab.kind === 'delivery' && resolved && resolved.scope !== 'shared' && !shotId && !filters.q.trim() && (
                <Deliveries projectId={project.id} episodeId={resolved.episode?.id} onPreview={onPreview} />
              )}
              {assets.isError && <p role="alert" className="error">素材加载失败：{(assets.error as Error).message}</p>}
              {assets.isSuccess && !cards.length && <p className="empty">当前范围内还没有素材</p>}
              {cards.map(card => <AssetCardView key={card.id} projectId={project.id} card={card} onPreview={onPreview} />)}
              {assets.hasNextPage && (
                <button type="button" className="more" disabled={assets.isFetchingNextPage}
                  onClick={() => assets.fetchNextPage()}>加载更多</button>
              )}
            </>
          )}
      </div>
    </>
  )
}

function Documents({ project, episodeId }: { project: ProjectDetail; episodeId: number | undefined }) {
  const episode = useQuery({
    queryKey: ['episode', project.id, episodeId],
    queryFn: () => api.episode(project.id, episodeId!),
    enabled: episodeId !== undefined,
  })
  const plan = project.plan
  if (!plan) return <p className="empty">全剧规划完成后显示剧本</p>
  return (
    <>
      <article className="asset-card document">
        <strong className="asset-title">{project.name} · 全剧规划</strong>
        <p className="document-text">{plan.world}</p>
        <p className="document-meta">视觉规范：{plan.visualGuide}</p>
        {plan.characters.map(character => (
          <p key={character.name} className="document-meta">{character.name}：{character.profile}</p>
        ))}
      </article>
      {episode.data && (
        <article className="asset-card document">
          <strong className="asset-title">第 {episode.data.number} 集 · {episode.data.title}</strong>
          <p className="document-meta">大纲：{episode.data.outline}</p>
          {episode.data.script
            ? <pre className="document-text">{episode.data.script}</pre>
            : <p className="empty">分集剧本尚未完成</p>}
        </article>
      )}
    </>
  )
}

function Deliveries({ projectId, episodeId, onPreview }: {
  projectId: string
  episodeId: number | undefined
  onPreview: ListProps['onPreview']
}) {
  const interval = usePollInterval()
  const deliveries = useQuery<DeliveryList>({
    queryKey: ['deliveries', projectId, episodeId],
    queryFn: () => api.deliveries(projectId, episodeId),
    refetchInterval: interval,
  })
  return (
    <>
      {deliveries.data?.results.map(group => (
        <article key={group.episode.id} className="delivery" aria-label={`第 ${group.episode.number} 集交付`}>
          <strong>第 {group.episode.number} 集 · {group.episode.title}</strong>
          {group.deliveries.filter(item => item.current).map(item => (
            <div key={item.id} className="delivery-files">
              {(['video', 'subtitles', 'timeline'] as const).map(slot => (
                <button key={slot} type="button" className="version"
                  onClick={() => onPreview({ version: item[slot], title: `第 ${group.episode.number} 集 · ${KIND_LABELS[slot]}` })}>
                  {slot === 'video' ? '成片' : KIND_LABELS[slot]} · {formatBytes(item[slot].sizeBytes)}
                </button>
              ))}
            </div>
          ))}
        </article>
      ))}
    </>
  )
}

function AssetCardView({ projectId, card, onPreview }: {
  projectId: string
  card: AssetCard
  onPreview: ListProps['onPreview']
}) {
  const [open, setOpen] = useState(false)
  const versions = useQuery({
    queryKey: ['asset-versions', projectId, card.id, card.versionCount],
    queryFn: () => api.assetVersions(projectId, card.id),
    enabled: open,
  })
  const scope = [card.episode ? `第 ${card.episode.number} 集` : '全剧共用', card.shot ? `镜头 ${card.shot.number}` : '']
    .filter(Boolean).join(' · ')
  return (
    <article className="asset-card" aria-label={card.title}>
      <button type="button" className="asset-main" onClick={() => onPreview({ version: card.latestVersion, title: card.title })}>
        <span className={`asset-cover ${card.kind}`}><span>{KIND_LABELS[card.kind] ?? card.kind}</span></span>
        <span className="asset-title">{card.title}</span>
        <span className="asset-meta">
          <span>{scope}</span>
          {card.latestVersion.adopted && <span className="adopted">已采用</span>}
          {card.delivery && <span className="badge">交付</span>}
        </span>
      </button>
      <details open={open} onToggle={event => setOpen((event.target as HTMLDetailsElement).open)}>
        <summary>{card.versionCount} 个版本</summary>
        {versions.data?.versions.map((version, index) => (
          <VersionButton key={version.id} version={version} label={`版本 ${card.versionCount - index}`}
            onClick={() => onPreview({ version, title: card.title })} />
        ))}
      </details>
    </article>
  )
}

function VersionButton({ version, label, onClick }: { version: AssetVersion; label: string; onClick: () => void }) {
  return (
    <button type="button" className="version" onClick={onClick}>
      {label} · {formatTime(version.createdAt)} · {version.slot}
      {version.adopted && <span className="adopted">已采用</span>}
    </button>
  )
}

function OutputList({ project, output, onBack, onPreview, listRef }: ListProps & {
  output: OutputView
  onBack: () => void
}) {
  const outputs = useQuery<TaskOutputs>({
    queryKey: ['task-outputs', project.id, output.id],
    queryFn: () => api.taskOutputs(project.id, output.id),
  })
  const back = useRef<HTMLButtonElement>(null)
  useEffect(() => back.current?.focus({ preventScroll: true }), [output.id])
  const versions = outputs.data?.versions ?? []
  return (
    <>
      <div className="output-heading">
        <button ref={back} type="button" onClick={onBack}>← 返回素材库</button>
        <strong>{output.name}</strong>
        <span className="asset-count">{versions.length} 个产出</span>
      </div>
      <div ref={listRef} className="asset-list">
        {outputs.isError && <p role="alert" className="error">产出加载失败：{(outputs.error as Error).message}</p>}
        {outputs.data?.documents.map(document => (
          <p key={document.document} className="asset-card document document-meta">
            {document.document === 'plan' ? '全剧规划' : document.document === 'script' ? '分集剧本' : '分集结构化'}
            {document.episode ? ` · 第 ${document.episode.number} 集` : ''}：在「剧本」中查看
          </p>
        ))}
        {versions.map(version => (
          <article key={version.id} className="asset-card" aria-label={version.asset.title}>
            <button type="button" className="asset-main" onClick={() => onPreview({ version, title: version.asset.title })}>
              <span className={`asset-cover ${version.asset.kind}`}><span>{KIND_LABELS[version.asset.kind]}</span></span>
              <span className="asset-title">{version.asset.title}</span>
              <span className="asset-meta">
                <span>{version.slot}</span>
                {version.asset.episode && <span>第 {version.asset.episode.number} 集</span>}
                {version.asset.shot && <span>镜头 {version.asset.shot.number}</span>}
                {version.adopted && <span className="adopted">已采用</span>}
              </span>
            </button>
          </article>
        ))}
        {outputs.isSuccess && !versions.length && !outputs.data.documents.length && <p className="empty">该任务没有媒体产出</p>}
      </div>
    </>
  )
}

const REFRESH_BEFORE_EXPIRY_MS = 30_000

export function refreshDelay(expiresAt: string | undefined, now = Date.now()) {
  if (!expiresAt) return false
  return Math.max(5_000, Date.parse(expiresAt) - now - REFRESH_BEFORE_EXPIRY_MS)
}

function PreviewDialog({ projectId, version, title, onClose }: {
  projectId: string
  version: AssetVersion
  title: string
  onClose: () => void
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const signed = useQuery({
    queryKey: ['preview', projectId, version.id],
    queryFn: () => api.preview(projectId, version.id),
    gcTime: 0,
    // Re-sign shortly before the signature's own expiry. Until then a fresh signature is kept, so focus
    // changes do not swap the URL under a playing video.
    staleTime: query => refreshDelay(query.state.data?.expiresAt) || 0,
    refetchInterval: query => refreshDelay(query.state.data?.expiresAt),
  })
  const [now, setNow] = useState(() => Date.now())
  const expiresAt = signed.data?.expiresAt
  useEffect(() => {
    if (!expiresAt) return
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, Date.parse(expiresAt) - Date.now()) + 50)
    return () => clearTimeout(timer)
  }, [expiresAt])
  useEffect(() => {
    dialog.current?.showModal()
  }, [])
  const kind = previewKind(version.contentType)
  const current = signed.data && Date.parse(signed.data.expiresAt) > now ? signed.data : null
  const url = current?.url
  return (
    <dialog ref={dialog} className="asset-dialog" aria-label={`预览 ${title}`} onClose={onClose}>
      <div className="dialog-heading">
        <h2>{title}</h2>
        <button type="button" aria-label="关闭预览" onClick={() => dialog.current?.close()}>×</button>
      </div>
      <p className="preview-meta">
        {formatTime(version.createdAt)} · {version.contentType} · {formatBytes(version.sizeBytes)}
        {version.adopted ? ' · 已采用' : ''}
      </p>
      <div className="preview-body">
        {signed.isError && (
          <p role="alert" className="error">
            预览不可用：{(signed.error as Error).message}
            <button type="button" onClick={() => signed.refetch()}>重新签发</button>
          </p>
        )}
        {signed.data && !current && !signed.isFetching && (
          <p className="error">预览地址已过期 <button type="button" onClick={() => signed.refetch()}>重新签发</button></p>
        )}
        {url && kind === 'image' && <img src={url} alt={title} />}
        {url && kind === 'video' && <video src={url} controls preload="metadata" aria-label={title} />}
        {url && kind === 'audio' && <audio src={url} controls preload="metadata" aria-label={title} />}
        {url && kind === 'file' && <a href={url} rel="noreferrer">下载文件</a>}
      </div>
    </dialog>
  )
}
