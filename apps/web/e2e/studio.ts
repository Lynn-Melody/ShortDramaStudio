import { execFileSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, type Page } from '@playwright/test'

const repository = resolve(import.meta.dirname, '../../..')
const fixture = readFileSync(resolve(import.meta.dirname, 'fixture.py'), 'utf8')

/**
 * Run the fixture inside the API environment. STUDIO_FIXTURE_SHELL selects how:
 * `compose` (default) execs into the Compose api service; `local` uses the repository's uv environment.
 */
export function studio<T>(command: Record<string, unknown>): T {
  const script = `${fixture}\nimport json as _json\nprint("FIXTURE:" + _json.dumps(run(${JSON.stringify(command)})))\n`
  // Execute the piped script directly so the shell never echoes interactive prompts.
  const runner = 'import sys; exec(compile(sys.stdin.read(), "e2e/fixture.py", "exec"), {"__name__": "fixture"})'
  const mode = process.env.STUDIO_FIXTURE_SHELL ?? 'compose'
  const [program, args] = mode === 'local'
    ? ['uv', ['run', '--locked', 'python', 'apps/backend/manage.py', 'shell', '-c', runner]]
    : ['docker', ['compose', 'exec', '-T', 'api', 'python', 'apps/backend/manage.py', 'shell', '-c', runner]]
  const output = execFileSync(program, args, { cwd: repository, input: script, encoding: 'utf8' })
  const line = output.split(/\r?\n/).find(text => text.startsWith('FIXTURE:'))
  if (!line) throw new Error(`Fixture produced no result:\n${output}`)
  return JSON.parse(line.slice('FIXTURE:'.length)) as T
}

export type FixtureState = { running: string[]; rootState: string; done?: { taskType: string; status: string }[] }

export function advance(projectId: string, options: { taskTypes?: string[]; fail?: string } = {}) {
  return studio<FixtureState>({ action: 'advance', projectId, ...options })
}

/** Wait for the real Worker to dispatch workflows of the given type, then answer them. */
export async function completeWhenRunning(projectId: string, taskType: string, options: { fail?: string } = {}) {
  await expect.poll(() => studio<FixtureState>({ action: 'state', projectId }).running, { timeout: 30_000 })
    .toContain(taskType)
  return advance(projectId, { taskTypes: [taskType], ...options })
}

export async function signIn(page: Page) {
  const account = studio<{ username: string; password: string }>({ action: 'user' })
  await page.goto('/')
  await page.getByLabel('账号').fill(account.username)
  await page.getByLabel('密码').fill(account.password)
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('button', { name: '＋ 新建' })).toBeVisible()
}

export async function createProject(page: Page, idea: string) {
  await page.getByRole('button', { name: '＋ 新建' }).click()
  await page.getByLabel('短剧 Idea').fill(idea)
  const created = page.waitForResponse(response =>
    response.url().endsWith('/api/v1/projects') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '创建项目' }).click()
  const response = await created
  expect(response.status()).toBe(201)
  return (await response.json()) as { id: string; rootTaskId: string }
}
