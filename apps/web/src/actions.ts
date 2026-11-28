import type { QueryClient } from '@tanstack/react-query'
import type { ProjectDetail, ProjectSummary, TaskDetail } from './api'

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

/** After sign-out keep only the fresh anonymous session; workbench data belongs to the old account. */
export function afterLogout(client: QueryClient, session: unknown) {
  client.removeQueries({ predicate: query => query.queryKey[0] !== keys.session[0] })
  client.setQueryData(keys.session, session)
}
