import type { ModelInfo, ModelTaskConfig, TaskConfig } from './types'

// NEKO: N08 gemini-fallback — validate the task-local target without changing candidates.
type FallbackModel = Pick<ModelInfo, 'name'> & Partial<Pick<ModelInfo, 'model_type'>>

export function geminiFallbackError(task: TaskConfig, models: FallbackModel[]): string | undefined {
  const mode = task.gemini_fallback_mode ?? 'legacy_grok'
  if (!['legacy_grok', 'custom', 'disabled'].includes(mode)) return 'Gemini 拦截回落方式无效，请重新选择。'
  if (mode !== 'custom') return
  const target = task.gemini_fallback_model
  if (!target) return '请选择本任务中的生成模型作为回落目标，或切换回落方式。'
  if (!task.model_list?.includes(target) || !models.some((model) => model.name === target && model.model_type === 'llm')) {
    return `回落目标「${target}」已失效，请重新选择本任务中的生成模型，或切换回落方式。`
  }
}

export function geminiFallbackIssues(tasks: ModelTaskConfig | null, models: FallbackModel[]) {
  return Object.entries(tasks ?? {}).flatMap(([taskName, task]) => {
    if (!task || taskName === 'web_search') return []
    const message = geminiFallbackError(task, models)
    return message ? [{ taskName, message }] : []
  })
}

export function assertGeminiFallbacks(tasks: ModelTaskConfig | null, models: FallbackModel[]) {
  const issues = geminiFallbackIssues(tasks, models)
  if (issues.length) throw new Error(issues.map(({ taskName, message }) => `${taskName}：${message}`).join('\n'))
}
