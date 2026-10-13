import { useEffect, useState } from 'react'
import { api, type ToolCallDetail } from '../api'

type Props = { toolCallId: string; onClose: () => void }

// F5: 증거 원본(마스킹 적용) 보기
export function EvidenceModal({ toolCallId, onClose }: Props) {
  const [detail, setDetail] = useState<ToolCallDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.toolCall(toolCallId).then(setDetail, (e: Error) => setError(e.message))
  }, [toolCallId])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fixed inset-0 z-10 flex items-center justify-center bg-slate-900/40 p-6" onClick={onClose}>
      <div
        role="dialog"
        data-testid="evidence-modal"
        className="flex max-h-full w-full max-w-4xl flex-col rounded-xl bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 border-b border-slate-200 px-5 py-3">
          <div className="flex-1">
            <h2 className="font-mono text-sm font-semibold">{detail?.tool_name ?? '불러오는 중…'}</h2>
            {detail && (
              <p className="mt-0.5 font-mono text-xs text-slate-500">
                {JSON.stringify(detail.args)} · {detail.status} · {detail.duration_ms}ms · id {detail.id}
              </p>
            )}
          </div>
          {detail?.truncated && (
            <span className="rounded bg-amber-100 px-2 py-0.5 text-xs text-amber-800">
              4,000자에서 절단됨
            </span>
          )}
          <button className="text-slate-400 hover:text-slate-700" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </div>
        {error && <p className="p-5 text-sm text-rose-700">{error}</p>}
        {detail && (
          <pre
            data-testid="evidence-output"
            className="flex-1 overflow-auto bg-slate-950 p-5 font-mono text-xs leading-5 text-slate-100"
          >
            {detail.output_text}
          </pre>
        )}
        <p className="border-t border-slate-200 px-5 py-2 text-xs text-slate-400">
          토큰·키 패턴은 마스킹(***)된 상태로 저장·표시됩니다.
        </p>
      </div>
    </div>
  )
}
