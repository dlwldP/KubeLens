export type TraceItem = {
  id: string
  ref?: string
  tool_name: string
  args: Record<string, unknown>
  status: 'running' | 'ok' | 'error' | string
  duration_ms?: number
  truncated?: boolean
}

type Props = {
  items: TraceItem[]
  plan: string | null
  status: string | null
  onSelect: (toolCallId: string) => void
}

function formatArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
    .join(', ')
}

// F3: 호출한 도구, 인자, 소요시간, 성공 여부를 실시간 표시
export function TracePanel({ items, plan, status, onSelect }: Props) {
  return (
    <aside className="flex w-96 shrink-0 flex-col border-l border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <h2 className="text-sm font-semibold">도구 트레이스</h2>
        {status && (
          <p data-testid="trace-status" className="mt-1 animate-pulse text-xs text-slate-500">
            {status}
          </p>
        )}
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        {plan && (
          <details className="mb-3 rounded-md bg-slate-50 p-2 text-xs text-slate-600" open>
            <summary className="cursor-pointer font-medium">조사 계획</summary>
            <p className="mt-1 whitespace-pre-wrap">{plan}</p>
          </details>
        )}
        {items.length === 0 && !plan && (
          <p className="text-sm text-slate-400">질문을 보내면 에이전트의 조회 과정이 여기에 표시됩니다.</p>
        )}
        <ol className="space-y-2" data-testid="trace-list">
          {items.map((t) => (
            <li key={t.id}>
              <button
                data-testid="trace-item"
                disabled={t.status === 'running'}
                className="w-full rounded-md border border-slate-200 p-2 text-left hover:bg-slate-50 disabled:cursor-wait"
                onClick={() => onSelect(t.id)}
              >
                <div className="flex items-center gap-2 text-sm">
                  <span>{t.status === 'running' ? '⏳' : t.status === 'ok' ? '✅' : '❌'}</span>
                  <span className="font-mono font-medium">{t.tool_name}</span>
                  {t.ref && <span className="font-mono text-xs text-slate-400">{t.ref}</span>}
                  <span className="ml-auto text-xs text-slate-500">
                    {t.duration_ms !== undefined ? `${t.duration_ms}ms` : ''}
                  </span>
                </div>
                <p className="mt-1 truncate font-mono text-xs text-slate-500" title={formatArgs(t.args)}>
                  {formatArgs(t.args) || '()'}
                </p>
                {t.truncated && (
                  <span className="mt-1 inline-block rounded bg-amber-100 px-1.5 text-xs text-amber-800">
                    truncated
                  </span>
                )}
              </button>
            </li>
          ))}
        </ol>
      </div>
    </aside>
  )
}
