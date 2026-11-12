import { act, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'

import { SubagentInspectorView } from './subagent-panel'
import { openSubagentPanel, setSubagentPanelState } from './subagent-panel-state'
import type { SubagentTaskInfo } from './use-maisaka-monitor'

const { tasks } = vi.hoisted(() => ({ tasks: [] as SubagentTaskInfo[] }))
vi.mock('./use-maisaka-monitor', () => ({
  useMaisakaMonitorSession: () => ({ subagentTasks: tasks }),
}))

it('同名任务和调用从成员工具卡定位到正确任务，并保留成员名称', () => {
  tasks.splice(0, tasks.length, ...['a', 'b'].map((member): SubagentTaskInfo => ({
    key: member, sessionId: member, sessionName: `聊天 ${member}`, focusScopeId: 'group:shared',
    taskId: 'subagent-2', callId: 'same-call', description: 'synthetic task',
    grantedTools: [], maxRounds: 20, status: 'completed', startedAt: 10, finishedAt: 11,
    rounds: [], toolUsage: {}, conclusion: `result ${member}`, errorMessage: '',
  })))
  setSubagentPanelState({ selectedTaskKey: null, focusCallId: null, focusSessionId: null })
  render(<SubagentInspectorView />)
  expect(screen.getByText('result a')).toBeInTheDocument()
  act(() => openSubagentPanel('same-call', 'b'))
  expect(screen.getByText('result b')).toBeInTheDocument()
  expect(screen.queryByText('result a')).not.toBeInTheDocument()
  expect(screen.getByText('聊天 a')).toBeInTheDocument()
  expect(screen.getByText('聊天 b')).toBeInTheDocument()
  act(() => openSubagentPanel('same-call')) // Ambiguous legacy links must not select the first member.
  expect(screen.getByText('result b')).toBeInTheDocument()
})
