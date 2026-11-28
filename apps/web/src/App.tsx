import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { type FormEvent, useEffect, useRef, useState } from 'react'
import { afterLogout, afterSwitch } from './actions'
import { AssetLibrary } from './AssetLibrary'
import { api, ApiError, type ProjectDetail, type ProjectSummary, rememberCsrf, type Session } from './api'
import { usePollInterval } from './hooks'
import { percent, PROJECT_STATUS, projectControl, projectNotes, storedTheme, type Theme, THEME_KEY } from './model'
import { TaskTree } from './TaskTree'

export function App() {
  const client = useQueryClient()
  const session = useQuery({ queryKey: ['session'], queryFn: api.session, retry: false })
  const [theme, setTheme] = useState<Theme>(() => storedTheme(globalThis.localStorage))

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      localStorage.setItem(THEME_KEY, theme)
    } catch {
      // Private browsing may refuse storage; the theme still applies for this page.
    }
  }, [theme])
  useEffect(() => {
    if (session.data) rememberCsrf(session.data)
  }, [session.data])

  const signedIn = (next: Session) => {
    rememberCsrf(next)
    client.setQueryData(['session'], next)
  }

  return (
    <>
      <header className="masthead">
        <div className="brand">
          <span className="brand-icon" aria-hidden="true">▥</span>
          <strong>片场</strong>
          <span className="brand-note">AI 短剧工作室</span>
        </div>
        <div className="header-actions">
          {session.data?.authenticated && <span className="workspace"><span className="signal" />{session.data.username}</span>}
          <button type="button" aria-label={theme === 'dark' ? '切换明亮主题' : '切换深色主题'}
            onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}>
            {theme === 'dark' ? '☀ 明亮' : '☾ 深色'}
          </button>
          {session.data?.authenticated && (
            <button type="button" onClick={async () => {
              await api.logout()
              const anonymous = await api.session()
              rememberCsrf(anonymous)
              afterLogout(client, anonymous)
            }}>退出</button>
          )}
        </div>
      </header>
      {session.isPending && <p className="splash" role="status">正在连接 API</p>}
      {session.isError && <p className="splash" role="alert">API 暂不可用</p>}
      {session.data && !session.data.authenticated && <Login onSignedIn={signedIn} />}
      {session.data?.authenticated && <Workbench />}
    </>
  )
}

function Login({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const login = useMutation({ mutationFn: () => api.login(username, password), onSuccess: onSignedIn })
  return (
    <main className="login">
      <form className="pane" onSubmit={event => {
        event.preventDefault()
        login.mutate()
      }}>
        <h1>登录工作台</h1>
        <label htmlFor="username">账号</label>
        <input id="username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required />
        <label htmlFor="password">密码</label>
        <input id="password" type="password" autoComplete="current-password" value={password}
          onChange={event => setPassword(event.target.value)} required />
        {login.isError && <p role="alert" className="error">{(login.error as Error).message === 'Invalid username or password.'
          ? '账号或密码错误' : `登录失败：${(login.error as Error).message}`}</p>}
        <button type="submit" className="primary" disabled={login.isPending}>登录</button>
      </form>
    </main>
  )
}

function Workbench() {
  const client = useQueryClient()
  const interval = usePollInterval()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [output, setOutput] = useState<{ id: string; name: string } | null>(null)
  const [creating, setCreating] = useState(false)
  const projects = useInfiniteQuery({
    queryKey: ['projects'],
    queryFn: ({ pageParam }) => api.projects(pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: page => page.nextCursor ?? undefined,
    refetchInterval: interval,
  })
  const list = projects.data?.pages.flatMap(page => page.results) ?? []
  const activeId = selectedId ?? list[0]?.id ?? null
  const project = useQuery({
    queryKey: ['project', activeId],
    queryFn: () => api.project(activeId!),
    enabled: activeId !== null,
    refetchInterval: interval,
  })

  const select = (id: string) => {
    setSelectedId(id)
    setOutput(null)
  }
  const created = async (detail: ProjectDetail) => {
    client.setQueryData(['project', detail.id], detail)
    await client.invalidateQueries({ queryKey: ['projects'] })
    select(detail.id)
  }

  return (
    <main className="layout">
      <aside className="projects pane" aria-label="项目">
        <div className="pane-heading">
          <h2>项目列表</h2>
          <button type="button" className="new-button" onClick={() => setCreating(true)}>＋ 新建</button>
        </div>
        <div className="project-list">
          {projects.isError && <p role="alert" className="error">项目加载失败：{(projects.error as Error).message}</p>}
          {projects.isSuccess && !list.length && <p className="empty">还没有项目，点击「＋ 新建」输入 Idea。</p>}
          {list.map(item => <ProjectItem key={item.id} project={item} selected={item.id === activeId} onSelect={select} />)}
          {projects.hasNextPage && (
            <button type="button" className="more" onClick={() => projects.fetchNextPage()}>加载更多</button>
          )}
        </div>
      </aside>
      {project.data ? (
        <>
          <section className="production" aria-label="项目制作">
            <ProjectHeader project={project.data} />
            <ReactFlowProvider key={project.data.id}>
              <TaskTree project={project.data} onShowOutputs={setOutput} />
            </ReactFlowProvider>
          </section>
          <AssetLibrary key={project.data.id} project={project.data} output={output}
            onCloseOutput={() => setOutput(null)} />
        </>
      ) : (
        <section className="production placeholder pane">
          {project.isError ? <p role="alert">项目加载失败：{(project.error as Error).message}</p>
            : <p className="empty">{activeId ? '正在读取项目' : '创建项目后在这里查看制作进度'}</p>}
        </section>
      )}
      <CreateDialog open={creating} onClose={() => setCreating(false)} onCreated={created} />
    </main>
  )
}

function ProjectItem({ project, selected, onSelect }: {
  project: ProjectSummary
  selected: boolean
  onSelect: (id: string) => void
}) {
  const status = PROJECT_STATUS[project.status]
  return (
    <button type="button" className="project-item" aria-pressed={selected} onClick={() => onSelect(project.id)}>
      <span className="project-name">{project.name}</span>
      <span className="project-meta">
        <span className={`state ${status.tone.replace('_', '-')}`}>{status.label}</span>
        <span>{percent(project.progress)}%</span>
      </span>
    </button>
  )
}

function ProjectHeader({ project }: { project: ProjectDetail }) {
  const client = useQueryClient()
  const control = projectControl(project)
  const status = PROJECT_STATUS[project.status]
  const toggle = useMutation({
    mutationFn: () => (control!.action === 'pause' ? api.pause(project.id) : api.resume(project.id)),
    onSuccess: summary => afterSwitch(client, summary),
  })
  const notes = projectNotes(project)
  const value = percent(project.progress)
  return (
    <div className="project-header pane">
      <div>
        <div className="project-kicker">项目概览</div>
        <h1>{project.name}</h1>
      </div>
      <div className="project-actions">
        <span className={`state ${status.tone.replace('_', '-')}`} role="status" aria-label={`项目状态：${status.label}`}>
          {status.label}
        </span>
        {control && (
          <button type="button" disabled={toggle.isPending} onClick={() => toggle.mutate()}>{control.label}</button>
        )}
      </div>
      <div className="progress-label"><span>制作进度</span><strong>{value}%</strong></div>
      <progress max={100} value={value} aria-label="制作进度" />
      {notes.length > 0 && <div className="project-summary">{notes.join(' · ')}</div>}
      {toggle.isError && <p role="alert" className="error project-summary">操作失败：{(toggle.error as Error).message}</p>}
    </div>
  )
}

function CreateDialog({ open, onClose, onCreated }: {
  open: boolean
  onClose: () => void
  onCreated: (project: ProjectDetail) => Promise<void>
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const [idea, setIdea] = useState('')
  // A stable client UUID makes a resubmitted creation replay instead of duplicating the project.
  const [projectId, setProjectId] = useState(() => crypto.randomUUID())
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (open && !element.open) element.showModal()
    if (!open && element.open) element.close()
  }, [open])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const project = await api.createProject(idea, projectId)
      setIdea('')
      setProjectId(crypto.randomUUID())
      onClose()
      await onCreated(project)
    } catch (exc) {
      setError(exc instanceof ApiError && exc.fields.idea ? `Idea 无效：${exc.fields.idea.join('；')}` : `创建失败：${(exc as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  return (
    <dialog ref={dialog} className="create-dialog" aria-labelledby="create-title" onClose={onClose}>
      <form onSubmit={submit}>
        <div className="dialog-heading">
          <h2 id="create-title">创建项目</h2>
          <button type="button" aria-label="关闭" onClick={onClose}>×</button>
        </div>
        <label htmlFor="idea">短剧 Idea</label>
        <textarea id="idea" rows={5} required value={idea} onChange={event => {
          setIdea(event.target.value)
          setProjectId(crypto.randomUUID())
        }} placeholder="例如：一名失业配音员发现，自己能听见角色没说出口的台词。" />
        {error && <p role="alert" className="error">{error}</p>}
        <div className="dialog-actions">
          <button type="submit" className="primary" disabled={busy || !idea.trim()}>创建项目</button>
        </div>
      </form>
    </dialog>
  )
}
