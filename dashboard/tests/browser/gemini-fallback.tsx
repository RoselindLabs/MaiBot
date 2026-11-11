// Synthetic browser fixture: real editor and save hook, all API traffic intercepted by the runner.
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../../src/index.css'
import '../../src/styles/controls.css'
import '../../src/styles/millennium.css'
import '../../src/styles/modern.css'
import '../../src/styles/mobile.css'
import '../../src/styles/surfaces.css'
import '../../src/i18n'
import { ThemeProvider } from '../../src/components/theme-provider'
import { AnimationProvider } from '../../src/components/animation-provider'
import { TooltipProvider } from '../../src/components/ui/tooltip'
import { TaskAssignmentBoard } from '../../src/routes/config/model/components/TaskAssignmentBoard'
import { useModelConfig } from '../../src/routes/config/model/hooks/useModelConfig'

function Fixture() {
  const config = useModelConfig()
  const [openTask, setOpenTask] = useState<string | null>('replyer')
  if (!config.taskConfig) return <p>加载合成配置</p>
  return <main className="p-4">
    <p className="mb-4">合成配置 · {config.hasUnsavedChanges ? '未保存' : '已保存'}</p>
    <TaskAssignmentBoard
      fields={['replyer', 'planner', 'utils', 'embedding'].map((name) => ({ name, type: 'object' }))}
      taskConfig={config.taskConfig} models={config.models} webServices={[]}
      openTask={openTask} onOpenTaskChange={setOpenTask} onChange={config.updateTaskConfig}
      hasProviders onAddWebService={() => {}} fallbackLabel={(field) => field.name}
    />
  </main>
}

createRoot(document.getElementById('root')!).render(
  <ThemeProvider defaultTheme="light"><AnimationProvider><TooltipProvider><Fixture /></TooltipProvider></AnimationProvider></ThemeProvider>
)
