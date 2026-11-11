// Run with a local Vite server on 7999. PLAYWRIGHT_PACKAGE can point to an existing
// @playwright/test/package.json without adding or resolving dashboard dependencies.
import { createRequire } from 'node:module'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'
const require = createRequire(process.env.PLAYWRIGHT_PACKAGE || import.meta.url)
const { chromium, expect } = require('@playwright/test')
const output = process.env.BROWSER_EVIDENCE_DIR
if (!output) throw new Error('BROWSER_EVIDENCE_DIR is required')
await mkdir(output, { recursive: true })
const browser = await chromium.launch({ channel: process.env.PW_BROWSER_CHANNEL || 'msedge' })
const model = (name, model_type = 'llm') => ({ name, model_identifier: `synthetic-${name}`, model_type, api_provider: 'fixture', price_in: 0, price_out: 0 })
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' })
    let config = {
      models: [model('Gemini 测试'), model('备用生成模型'), model('未分配模型'), model('嵌入模型', 'embedding')],
      api_providers: [{ name: 'fixture', base_url: 'https://fixture.invalid', api_key: '', client_type: 'gemini' }],
      model_task_config: { replyer: { model_list: ['Gemini 测试', '备用生成模型', '嵌入模型'] }, planner: { model_list: [] }, utils: { model_list: [] }, embedding: { model_list: ['嵌入模型'] } },
    }
    const writes = []
    const blocked = []
    await context.route('**/*', async (route) => {
      const url = new URL(route.request().url())
      if (url.origin !== 'http://127.0.0.1:7999') { blocked.push(url.origin); return route.abort() }
      if (!url.pathname.startsWith('/api/')) return route.continue()
      const reply = (json) => route.fulfill({ json })
      if (url.pathname.endsWith('/schema/model')) return reply({ schema: { nested: {} } })
      if (url.pathname.endsWith('/model/versions')) return reply({ active_version: null, versions: [] })
      if (url.pathname.endsWith('/model') && route.request().method() === 'GET') return reply({ config })
      if (url.pathname.endsWith('/model/section/model_task_config') && route.request().method() === 'POST') {
        const tasks = route.request().postDataJSON()
        writes.push(structuredClone(tasks))
        config = { ...config, model_task_config: tasks }
        return reply({ success: true })
      }
      throw new Error(`Unexpected synthetic API: ${route.request().method()} ${url.pathname}`)
    })
    const page = await context.newPage()
    const errors = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto('http://127.0.0.1:7999/tests/browser/gemini-fallback.html')
    const mode = page.getByRole('combobox', { name: '回落方式' })
    const target = page.getByRole('combobox', { name: '回落目标' })
    await expect(mode).toContainText('默认（Grok）')
    await expect(target).toHaveCount(0)
    await mode.click()
    await page.getByRole('option', { name: '指定模型', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('请选择本任务中的生成模型')
    await page.waitForTimeout(2200)
    expect(writes).toHaveLength(0)
    await target.click()
    await expect(page.getByRole('option', { name: '未分配模型', exact: true })).toHaveCount(0)
    await expect(page.getByRole('option', { name: '嵌入模型', exact: true })).toHaveCount(0)
    await page.getByRole('option', { name: '备用生成模型', exact: true }).click()
    await expect.poll(() => writes.length).toBe(1)
    expect(writes[0].replyer.gemini_fallback_model).toBe('备用生成模型')
    expect(writes[0].planner).toEqual({ model_list: [] })
    await page.reload()
    await expect(mode).toContainText('指定模型')
    await expect(target).toContainText('备用生成模型')
    await mode.scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(output, `gemini-fallback-${width}.png`) })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await target.click()
    await page.screenshot({ path: path.join(output, `gemini-target-${width}.png`) })
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: '移除 备用生成模型', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('已失效')
    await page.waitForTimeout(2200)
    expect(writes).toHaveLength(1)
    await page.getByRole('alert').scrollIntoViewIfNeeded()
    await page.screenshot({ path: path.join(output, `gemini-invalid-${width}.png`) })
    await mode.click()
    await page.getByRole('option', { name: '关闭', exact: true }).click()
    await expect(target).toHaveCount(0)
    await expect.poll(() => writes.length).toBe(2)
    expect(writes[1].replyer.gemini_fallback_model).toBe('')
    expect(writes[1].replyer.gemini_fallback_mode).toBe('disabled')
    await mode.click()
    await page.getByRole('option', { name: '默认（Grok）', exact: true }).click()
    await expect.poll(() => writes.length).toBe(3)
    expect(writes[2].replyer.gemini_fallback_model).toBe('')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(errors).toEqual([])
    expect(blocked).toEqual([])
    console.log(`PASS ${width}px: custom/reload/invalid/disabled/legacy, 3 serialized writes, no external requests`)
    await context.close()
  }
} finally { await browser.close() }
