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
    buffer += value
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      let type = ''
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) type = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (type && data) onEvent({ type, data: JSON.parse(data) } as StreamEvent)
    }
  }
}
