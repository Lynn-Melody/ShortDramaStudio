import { expect, test } from '@playwright/test'

test('the React workbench reaches Django through Vite', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '短剧工作室' })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('API 已连接')
})
