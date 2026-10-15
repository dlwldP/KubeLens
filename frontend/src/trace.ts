import type { Message } from './api'
import type { TraceItem } from './components/TracePanel'

/** 저장된 도구 호출을 트레이스 항목으로. 표시 이름은 실행 순서대로 tc_1, tc_2 … */
export function traceFromMessage(m: Message): TraceItem[] {
  return m.tool_calls.map((t, i) => ({ ...t, ref: `tc_${i + 1}` }))
}

/** ToolCall.id → 표시 이름(tc_N). 진단 카드의 증거 버튼에 쓴다. */
export function evidenceLabels(m: Message): Record<string, string> {
  return Object.fromEntries(traceFromMessage(m).map((t) => [t.id, t.ref ?? t.id]))
}
