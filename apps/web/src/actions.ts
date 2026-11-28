import type { QueryClient } from '@tanstack/react-query'
import type { ProjectDetail, ProjectSummary, Session, TaskDetail } from './api'

export const keys = {
  session: ['session'] as const,
  projects: ['projects'] as const,
  project: (projectId: string) => ['project', projectId] as const,
  tree: (projectId: string) => ['task-tree', projectId] as const,
  task: (projectId: string, taskId: string) => ['task', projectId, taskId] as const,
  tasks: (projectId: string) => ['task', projectId] as const,
}

/**
 * Apply an action's response without letting an older in-flight poll overwrite it:
 * cancel reads that started before the action, write the response, then refresh in parallel.
 */
async function settle(client: QueryClient, write: () => void, refresh: readonly (readonly unknown[])[]) {
  await Promise.all(refresh.map(queryKey => client.cancelQueries({ queryKey })))
  write()
  await Promise.all(refresh.map(queryKey => client.invalidateQueries({ queryKey })))
}

export function afterRetry(client: QueryClient, projectId: string, detail: TaskDetail) {
  return settle(client, () => client.setQueryData(keys.task(projectId, detail.id), detail), [
    keys.tasks(projectId), keys.tree(projectId), keys.project(projectId), keys.projects,
  ])
}

export function afterSwitch(client: QueryClient, summary: ProjectSummary) {
  return settle(client, () => client.setQueryData<ProjectDetail>(
    keys.project(summary.id), current => current && { ...current, ...summary },
  ), [keys.project(summary.id), keys.projects, keys.tree(summary.id)])
}

/**
 * After sign-out keep only an anonymous session; workbench data belongs to the old account.
 * An older in-flight session read is cancelled first so it cannot restore the signed-in state.
 */
export async function afterLogout(client: QueryClient, session: Session | null) {
  await client.cancelQueries({ queryKey: keys.session })
  client.removeQueries({ predicate: query => query.queryKey[0] !== keys.session[0] })
  client.setQueryData<Session>(keys.session, session ?? { authenticated: false, username: null, csrfToken: '' })
}
