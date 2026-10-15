import { describe, expect, it } from 'vitest'
import type { Message } from './api'
import { evidenceLabels, traceFromMessage } from './trace'

const call = (id: string, tool_name: string) => ({
  id,
  message_id: 2,
  tool_name,
  args: {},
  truncated: false,
  duration_ms: 3,
  status: 'ok',
  created_at: '2026-10-15T00:00:00Z',
})

const message: Message = {
  id: 2,
  session_id: 1,
  role: 'assistant',
  content: '요약',
  result: null,
  tool_calls: [call('f00d', 'list_pods'), call('beef', 'get_pod_logs')],
  created_at: '2026-10-15T00:00:00Z',
}

describe('trace', () => {
  it('실행 순서대로 tc_N 이름을 붙인다', () => {
    expect(traceFromMessage(message).map((t) => [t.tool_name, t.ref])).toEqual([
      ['list_pods', 'tc_1'],
      ['get_pod_logs', 'tc_2'],
    ])
  })

  it('증거 버튼 라벨은 DB id를 tc_N으로 바꾼다', () => {
    expect(evidenceLabels(message)).toEqual({ f00d: 'tc_1', beef: 'tc_2' })
  })
})
