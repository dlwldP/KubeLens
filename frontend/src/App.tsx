import { useCallback, useEffect, useState } from 'react'
import {
  api,
  streamMessage,
  type Cluster,
  type Message,
  type SessionDetail,
  type SessionSummary,
  type StreamEvent,
} from './api'
import { ClusterSetup } from './components/ClusterSetup'
import { DiagnosisCard } from './components/DiagnosisCard'
import { EvidenceModal } from './components/EvidenceModal'
import { SessionList } from './components/SessionList'
import { TracePanel, type TraceItem } from './components/TracePanel'

type Live = {
  question: string
  plan: string | null
  status: string | null
  trace: TraceItem[]
  error: string | null
}

const EXAMPLES = [
  'kubelens-chaos 네임스페이스의 payment-api가 계속 재시작돼. 왜 그래?',
  'batch-trainer가 계속 Pending이야. 왜 스케줄이 안 돼?',
  '지금 클러스터에 문제 있는 거 있어?',
]

function traceFromMessage(m: Message): TraceItem[] {
  return m.tool_calls.map((t, i) => ({ ...t, ref: `tc_${i + 1}` }))
}

export default function App() {
  const [clusters, setClusters] = useState<Cluster[] | null>(null)
  const [cluster, setCluster] = useState<Cluster | null>(null)
  const [settingUp, setSettingUp] = useState(false)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [selectedMessageId, setSelectedMessageId] = useState<number | null>(null)
  const [live, setLive] = useState<Live | null>(null)
  const [evidenceId, setEvidenceId] = useState<string | null>(null)
  const [question, setQuestion] = useState('')
  const [namespace, setNamespace] = useState('')

  useEffect(() => {
    api.clusters().then((cs) => {
      setClusters(cs)
      setCluster(cs[0] ?? null)
    })
    void api.sessions().then(setSessions)
  }, [])

  const openSession = useCallback(async (id: number) => {
    setSessionId(id)
    const d = await api.session(id)
    setDetail(d)
    const lastAssistant = [...d.messages].reverse().find((m) => m.role === 'assistant')
    setSelectedMessageId(lastAssistant?.id ?? null)
  }, [])

  const closeEvidence = useCallback(() => setEvidenceId(null), [])

  if (clusters === null) return <p className="p-8 text-sm text-slate-500">불러오는 중…</p>

  if (!cluster || settingUp) {
    return (
      <ClusterSetup
        onCancel={cluster ? () => setSettingUp(false) : undefined}
        onReady={(c) => {
          setClusters([c, ...clusters])
          setCluster(c)
          setSettingUp(false)
        }}
      />
    )
  }

  function onEvent(e: StreamEvent) {
    setLive((prev) => {
      if (!prev) return prev
      switch (e.type) {
        case 'thinking':
          return { ...prev, status: e.data.message, plan: e.data.plan ?? prev.plan }
        case 'tool_start':
          return {
            ...prev,
            status: `${e.data.tool_name} 실행 중`,
            trace: [...prev.trace, { ...e.data, status: 'running' }],
          }
        case 'tool_end':
          return {
            ...prev,
            trace: prev.trace.map((t) => (t.id === e.data.id ? { ...t, ...e.data } : t)),
          }
        case 'error':
          return { ...prev, error: e.data.message, status: null }
        case 'final':
          return { ...prev, status: null }
      }
    })
  }

  async function send(text: string) {
    if (!cluster || !text.trim() || live) return
    let sid = sessionId
    if (sid === null) {
      const s = await api.createSession(cluster.id)
      sid = s.id
      setSessionId(sid)
      setDetail({ ...s, messages: [] })
    }
    setQuestion('')
    setLive({ question: text, plan: null, status: '요청 전송 중', trace: [], error: null })
    let failed: string | null = null
    try {
      await streamMessage(sid, text, namespace.trim() || null, onEvent)
    } catch (err) {
      failed = (err as Error).message
    }
    await openSession(sid)
    setSessions(await api.sessions())
    setLive((prev) => (failed && prev ? { ...prev, error: failed } : null))
  }

  const selectedMessage = detail?.messages.find((m) => m.id === selectedMessageId) ?? null
  const traceItems = live ? live.trace : selectedMessage ? traceFromMessage(selectedMessage) : []

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-4 py-2.5">
        <span className="font-semibold">🔍 KubeLens</span>
        <span className="rounded bg-emerald-50 px-2 py-0.5 text-xs text-emerald-700">read-only</span>
        <span className="ml-auto text-sm text-slate-500" data-testid="cluster-badge">
          {cluster.name}
          {cluster.context ? ` · ${cluster.context}` : ''}
          {cluster.server_version ? ` · ${cluster.server_version}` : ''}
        </span>
        <button
          className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
          onClick={() => setSettingUp(true)}
        >
          클러스터 설정
        </button>
      </header>

      <div className="flex min-h-0 flex-1">
        <SessionList
          sessions={sessions}
          activeId={sessionId}
          onSelect={(id) => void openSession(id)}
          onNew={() => {
            setSessionId(null)
            setDetail(null)
            setSelectedMessageId(null)
          }}
        />

        <main className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 space-y-4 overflow-y-auto p-6" data-testid="chat">
            {!detail?.messages.length && !live && (
              <div className="mx-auto mt-10 max-w-lg text-center">
                <h2 className="text-lg font-semibold">무엇이 문제인가요?</h2>
                <p className="mt-1 text-sm text-slate-500">
                  에이전트가 events·logs·describe를 조회하고, 근거가 붙은 원인 후보를 보여줍니다.
                </p>
                <div className="mt-4 space-y-2">
                  {EXAMPLES.map((q) => (
                    <button
                      key={q}
                      className="block w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-sm hover:bg-slate-50"
                      onClick={() => setQuestion(q)}
                    >
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {detail?.messages.map((m) =>
              m.role === 'user' ? (
                <UserBubble key={m.id} text={m.content} />
              ) : (
                <div
                  key={m.id}
                  className={`cursor-pointer rounded-xl ring-offset-2 ${m.id === selectedMessageId && !live ? 'ring-2 ring-sky-200' : ''}`}
                  onClick={() => setSelectedMessageId(m.id)}
                >
                  {m.result ? (
                    <DiagnosisCard
                      result={m.result}
                      onEvidence={setEvidenceId}
                      labels={Object.fromEntries(traceFromMessage(m).map((t) => [t.id, t.ref ?? t.id]))}
                    />
                  ) : (
                    <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-800">{m.content || '응답 없음'}</p>
                  )}
                </div>
              ),
            )}

            {live && (
              <>
                <UserBubble text={live.question} />
                {live.error ? (
                  <p className="rounded-xl bg-rose-50 p-4 text-sm text-rose-800">진단 실패: {live.error}</p>
                ) : (
                  <div className="rounded-xl border border-dashed border-slate-300 bg-white p-4 text-sm text-slate-500">
                    진단 중… {live.status} (도구 {live.trace.length}회)
                  </div>
                )}
              </>
            )}
          </div>

          <form
            className="border-t border-slate-200 bg-white p-4"
            onSubmit={(e) => {
              e.preventDefault()
              void send(question)
            }}
          >
            <div className="flex gap-2">
              <input
                className="w-44 rounded-md border border-slate-300 px-3 py-2 text-sm"
                placeholder="네임스페이스 (선택)"
                value={namespace}
                onChange={(e) => setNamespace(e.target.value)}
              />
              <input
                data-testid="question-input"
                className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
                placeholder="예: payment 네임스페이스 api 파드가 계속 재시작돼"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
              />
              <button
                type="submit"
                className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
                disabled={!question.trim() || live !== null}
              >
                {live ? '진단 중' : '질문'}
              </button>
            </div>
          </form>
        </main>

        <TracePanel
          items={traceItems}
          plan={live?.plan ?? null}
          status={live?.status ?? null}
          onSelect={setEvidenceId}
        />
      </div>

      {evidenceId && <EvidenceModal toolCallId={evidenceId} onClose={closeEvidence} />}
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <p className="max-w-xl rounded-2xl bg-slate-900 px-4 py-2 text-sm text-white">{text}</p>
    </div>
  )
}
