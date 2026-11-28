import { expect, type Page, test } from '@playwright/test'
import { completeWhenRunning, createProject, signIn, snapshot } from './studio'

const header = (page: Page) => page.locator('.project-header')
const canvas = (page: Page) => page.getByLabel('任务画布')
const node = (page: Page, name: string) => canvas(page).getByRole('group', { name: new RegExp(`^${name}，`) })

test.describe.configure({ mode: 'serial' })

test('create, start, watch progress and open a registered asset version', async ({ page }) => {
  await signIn(page)

  // The idea draft survives closing the dialog.
  const idea = `A conductor follows the last train home. ${Date.now()}`
  await page.getByRole('button', { name: '＋ 新建' }).click()
  await page.getByLabel('短剧 Idea').fill(idea)
  await page.getByRole('button', { name: '关闭' }).click()
  await page.getByRole('button', { name: '＋ 新建' }).click()
  await expect(page.getByLabel('短剧 Idea')).toHaveValue(idea)
  await page.getByRole('button', { name: '关闭' }).click()
  const project = await createProject(page, idea)

  // The new project is selected, paused, and shows its waiting root.
  await expect(header(page).getByRole('heading')).toHaveText(idea)
  await expect(header(page).getByRole('status')).toHaveText('已暂停')
  await expect(page.locator('.project-item[aria-pressed="true"]')).toContainText(idea)
  await expect(node(page, '短剧制作')).toBeVisible()
  await expect(node(page, '短剧制作')).toHaveAttribute('data-state', 'waiting')

  await header(page).getByRole('button', { name: '▶ 启动制作' }).click()
  await expect(header(page).getByRole('status')).toHaveText('运行中')
  await expect(node(page, '全剧规划')).toHaveAttribute('data-state', 'running')

  // The running node shows its name on hover; single click opens details.
  await node(page, '全剧规划').hover()
  await expect(node(page, '全剧规划').getByRole('tooltip')).toHaveText('全剧规划')
  await node(page, '全剧规划').click()
  const detail = page.getByRole('dialog', { name: '任务详情' })
  await expect(detail).toContainText('全剧规划')
  await expect(detail).toContainText('运行')
  await expect(detail.getByRole('button', { name: '重试' })).toHaveCount(0)

  await completeWhenRunning(project.id, 'series_plan')
  await expect(header(page).getByRole('heading')).toHaveText('Night Service')
  await expect(header(page).locator('.progress-label strong')).toHaveText('33%')

  await completeWhenRunning(project.id, 'scene_asset')
  await completeWhenRunning(project.id, 'character_asset')
  await expect(header(page)).toContainText('当前第 1 集')

  // The shared library lists the registered character with both versions; preview signs a private read.
  const library = page.getByRole('region', { name: '素材库' })
  await library.getByLabel('范围').selectOption('shared')
  await library.getByRole('button', { name: '角色、场景与道具' }).click()
  const card = library.getByRole('article', { name: 'mei' })
  await expect(card).toContainText('已采用')
  await card.getByText('2 个版本').click()
  await expect(card.getByRole('button', { name: /版本 1/ })).toBeVisible()
  const preview = page.getByRole('dialog', { name: '预览 mei' })
  const image = preview.getByRole('img', { name: 'mei' })
  const width = () => image.evaluate(element => (element as HTMLImageElement).naturalWidth)
  // Version 2 is the adopted 1x1 image; version 1 is the 2x1 candidate. Each preview signs its own version.
  for (const [label, expected] of [['版本 2', 1], ['版本 1', 2]] as const) {
    const signed = page.waitForResponse(response => /\/asset-versions\/[^/]+\/preview$/.test(response.url()))
    await card.getByRole('button', { name: new RegExp(label) }).click()
    const body = await (await signed).json()
    expect(body.url).toContain('X-Amz-Signature')
    await expect(image).toBeVisible()
    await expect.poll(width).toBe(expected)
    await expect(preview).toContainText('image/png')
    await preview.getByRole('button', { name: '关闭预览' }).click()
  }
})

test('pause lets running work finish, drill-down returns, outputs and theme keep the view', async ({ page }) => {
  await signIn(page)
  const project = await createProject(page, `Pause and resume ${Date.now()}`)
  await header(page).getByRole('button', { name: '▶ 启动制作' }).click()
  await expect(node(page, '全剧规划')).toHaveAttribute('data-state', 'running')

  await header(page).getByRole('button', { name: 'Ⅱ 暂停' }).click()
  await expect(header(page).getByRole('status')).toHaveText('已暂停')
  await expect(header(page)).toContainText('1 个运行中的任务正在收尾')
  await completeWhenRunning(project.id, 'series_plan')
  // The running plan finished while paused; across several Worker cycles nothing new is claimed.
  await expect(header(page).locator('.progress-label strong')).toHaveText('33%')
  const settled = snapshot(project.id)
  expect(settled.running).toEqual([])
  for (let check = 0; check < 4; check++) {
    await page.waitForTimeout(1000)
    expect(snapshot(project.id)).toMatchObject({ running: [], executions: settled.executions })
  }
  await expect(node(page, '短剧制作')).toBeVisible()
  await expect(canvas(page).getByRole('group')).toHaveCount(1)

  await header(page).getByRole('button', { name: '▶ 继续' }).click()
  await expect.poll(() => snapshot(project.id).executions).toBeGreaterThan(settled.executions)
  await expect(node(page, '全局关键素材')).toBeVisible()
  await expect(canvas(page).getByRole('group')).toHaveCount(5)

  // Shared ancestors appear once for the three running asset tasks.
  await expect(node(page, '短剧制作')).toHaveCount(1)
  await expect(node(page, '全局关键素材')).toHaveCount(1)

  // Double-click drills into a two-level subtree; the back control returns and restores the zoom.
  await page.getByRole('button', { name: '放大' }).click()
  const zoom = await page.locator('.view-tools span').textContent()
  await node(page, '短剧制作').dblclick()
  await expect(page.locator('.graph-heading h2')).toHaveText('短剧制作')
  await expect(node(page, '全剧规划')).toHaveAttribute('data-state', 'completed')
  await node(page, '全局关键素材').dblclick()
  await expect(page.locator('.graph-heading h2')).toHaveText('全局关键素材')
  await page.getByRole('button', { name: '返回上一层' }).click()
  await expect(page.locator('.graph-heading h2')).toHaveText('短剧制作')
  await page.getByRole('button', { name: '返回上一层' }).click()
  await expect(page.locator('.graph-heading h2')).toHaveText('运行路径')
  await expect(node(page, '短剧制作')).toBeFocused()
  await page.keyboard.press('ArrowRight')
  await expect(page.locator('.graph-heading h2')).toHaveText('短剧制作')
  await expect(node(page, '短剧制作')).toBeFocused()
  await page.getByRole('button', { name: '返回上一层' }).click()
  await expect(page.locator('.graph-heading h2')).toHaveText('运行路径')
  await expect(page.locator('.view-tools span')).toHaveText(zoom!)
  await expect(page.getByRole('button', { name: '返回上一层' })).toHaveCount(0)

  // A task's outputs replace the library and the back button restores the filters.
  await completeWhenRunning(project.id, 'scene_asset')
  const library = page.getByRole('region', { name: '素材库' })
  await library.getByLabel('范围').selectOption('all')
  await expect(library.getByRole('article').first()).toBeVisible()
  const list = library.locator('.library-body .asset-list')
  await list.evaluate(element => { element.style.maxHeight = '160px' })
  await list.evaluate(element => { element.scrollTop = 120 })
  const scrolled = await list.evaluate(element => element.scrollTop)
  expect(scrolled).toBeGreaterThan(0)
  await node(page, '短剧制作').dblclick()
  await node(page, '全局关键素材').dblclick()
  const station = canvas(page).getByRole('group', { name: /^场景素材 · Station，完成/ })
  await station.click()
  await page.getByRole('button', { name: '查看 2 个产出' }).click()
  await expect(library.getByRole('button', { name: '← 返回素材库' })).toBeFocused()
  await expect(library.locator('.output-heading + .asset-list').getByRole('article')).toHaveCount(2)
  await library.getByRole('button', { name: '← 返回素材库' }).click()
  await expect(library.getByLabel('范围')).toHaveValue('all')
  expect(await list.evaluate(element => element.scrollTop)).toBe(scrolled)
  await library.getByLabel('搜索素材名称').fill('station')

  // Theme switches keep the subtree and filters, and persist across reloads.
  await page.getByRole('button', { name: '切换深色主题' }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('.graph-heading h2')).toHaveText('全局关键素材')
  await expect(library.getByLabel('搜索素材名称')).toHaveValue('station')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await page.getByRole('button', { name: '切换明亮主题' }).click()
})

test('failed tasks offer retry, and other states hide it', async ({ page }) => {
  await signIn(page)
  const project = await createProject(page, `Retry feedback ${Date.now()}`)
  await header(page).getByRole('button', { name: '▶ 启动制作' }).click()
  await completeWhenRunning(project.id, 'series_plan', { fail: 'series_plan' })

  await expect(header(page)).toContainText('1 个任务失败待处理')
  await node(page, '短剧制作').dblclick()
  await node(page, '全剧规划').click()
  const detail = page.getByRole('dialog', { name: '任务详情' })
  await expect(detail).toContainText('fixture_rejected')
  await detail.getByRole('button', { name: '重试' }).click()
  await expect(detail.getByRole('button', { name: '重试' })).toHaveCount(0)
  await expect(detail).toContainText(/运行|等待派发/)
  await completeWhenRunning(project.id, 'series_plan')
  await expect(detail).toContainText('完成')
})

test('invalid sign-in and empty ideas report errors', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('账号').fill('nobody')
  await page.getByLabel('密码').fill('wrong-password')
  await page.getByRole('button', { name: '登录' }).click()
  await expect(page.getByRole('alert')).toHaveText('账号或密码错误')

  await signIn(page)
  await page.getByRole('button', { name: '＋ 新建' }).click()
  await page.getByLabel('短剧 Idea').fill('   ')
  await expect(page.getByRole('button', { name: '创建项目' })).toBeDisabled()
  await page.getByRole('button', { name: '关闭' }).click()

  await page.getByRole('button', { name: '退出' }).click()
  await expect(page.getByRole('button', { name: '登录' })).toBeVisible()
  await expect(page.getByRole('region', { name: '项目' })).toHaveCount(0)
  await signIn(page)
})
