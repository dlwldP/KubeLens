// KubeLens API 클라이언트 (backend/kubelens/api/app.py와 짝).

export type Cluster = {
  id: number
  name: string
  kubeconfig_path: string
  context: string | null
  server_version: string | null
  created_at: string
}

export type KubeContext = { name: string; cluster: string | null; current: boolean }

export type SessionSummary = { id: number; cluster_id: number; title: string; created_at: string }

export type Confidence = 'high' | 'medium' | 'low'

export type RootCause = {
  title: string
  confidence: Confidence
  explanation: string
  evidence_refs: string[]
}

export type NextAction = { description: string; command: string | null }

export type Diagnosis = { summary: string; root_causes: RootCause[]; next_actions: NextAction[] }

export type DiagnosisResult = {
  diagnosis: Diagnosis | null
  valid: boolean
  validation_errors: string[]
  usage: Record<string, number>
  /** 시간 상한으로 증거 수집을 일찍 끝냈는지 (이전 버전 기록에는 없음) */
  timed_out?: boolean
}

export type ToolCallSummary = {
  id: string
  message_id: number
  tool_name: string
  args: Record<string, unknown>
  truncated: boolean
  duration_ms: number
  status: string
  created_at: string
}

export type ToolCallDetail = ToolCallSummary & { output_text: string }

export type Message = {
  id: number
  session_id: number
  role: 'user' | 'assistant'
  content: string
  result: DiagnosisResult | null
  tool_calls: ToolCallSummary[]
  created_at: string
}

export type SessionDetail = SessionSummary & { messages: Message[] }

export type StreamEvent =
  | { type: 'thinking'; data: { message: string; plan?: string; attempt?: number } }
  | {
      type: 'tool_start'
      data: { id: string; ref: string; tool_name: string; args: Record<string, unknown> }
    }
  | {
      type: 'tool_end'
      data: {
        id: string
        ref: string
        tool_name: string
        status: string
        duration_ms: number
        truncated: boolean
      }
    }
  | { type: 'final'; data: DiagnosisResult & { message_id: number } }
  | { type: 'error'; data: { message_id?: number; message: string } }

/**
 * SSE 텍스트를 이벤트 단위(빈 줄 구분)로 자른다. 마지막의 덜 받은 조각은 rest로 돌려준다.
 * data 줄이 여러 개면 줄바꿈으로 이어 붙인다(SSE 규격).
 */
export function parseSSE(buffer: string): { events: StreamEvent[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  const rest = blocks.pop() ?? ''
  const events: StreamEvent[] = []
  for (const block of blocks) {
    let type = ''
    const data: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) type = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
    }
    if (type && data.length) events.push({ type, data: JSON.parse(data.join('\n')) } as StreamEvent)
  }
  return { events, rest }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      detail = ((await res.json()) as { detail?: string }).detail ?? detail
    } catch {
      /* 본문이 JSON이 아님 */
    }
    throw new Error(`${res.status} ${detail}`)
  }
  return (await res.json()) as T
}

export const api = {
  contexts: (kubeconfigPath?: string) =>
    request<{ kubeconfig_path: string; contexts: KubeContext[] }>(
      `/api/kube/contexts${kubeconfigPath ? `?kubeconfig_path=${encodeURIComponent(kubeconfigPath)}` : ''}`,
    ),
  clusters: () => request<Cluster[]>('/api/clusters'),
  createCluster: (body: { name: string; kubeconfig_path?: string; context?: string }) =>
    request<Cluster>('/api/clusters', { method: 'POST', body: JSON.stringify(body) }),
  testCluster: (id: number) =>
    request<{ ok: boolean; message: string; server_version?: string }>(
      `/api/clusters/${id}/test`,
      { method: 'POST' },
    ),
  sessions: () => request<SessionSummary[]>('/api/sessions'),
  createSession: (clusterId: number) =>
    request<SessionSummary>('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ cluster_id: clusterId }),
    }),
  session: (id: number) => request<SessionDetail>(`/api/sessions/${id}`),
  toolCall: (id: string) => request<ToolCallDetail>(`/api/toolcalls/${id}`),
}

/** 질문을 보내고 SSE 이벤트를 순서대로 콜백한다. (POST라 EventSource 대신 fetch 스트림을 파싱) */
export async function streamMessage(
  sessionId: number,
  content: string,
  namespace: string | null,
  onEvent: (event: StreamEvent) => void,
): Promise<void> {
  const res = await fetch(`/api/sessions/${sessionId}/messages`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content, namespace }),
  })
  if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`)

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    const parsed = parseSSE(buffer + value)
    buffer = parsed.rest
    parsed.events.forEach(onEvent)
  }
}
