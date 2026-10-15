import { useState } from 'react'
import type { Confidence, DiagnosisResult } from '../api'

const CONFIDENCE_STYLE: Record<Confidence, string> = {
  high: 'bg-rose-100 text-rose-800',
  medium: 'bg-amber-100 text-amber-800',
  low: 'bg-slate-100 text-slate-700',
}

type Props = {
  result: DiagnosisResult
  onEvidence: (toolCallId: string) => void
  /** ToolCall.id → 트레이스 패널과 같은 표시 이름(tc_N) */
  labels?: Record<string, string>
}

// F4: 요약 / 원인 후보(신뢰도, 증거 링크) / 다음 조치(명령어 제안만)
export function DiagnosisCard({ result, onEvidence, labels = {} }: Props) {
  const d = result.diagnosis
  if (!d) return null
  return (
    <div data-testid="diagnosis-card" className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <p className="text-sm leading-6">{d.summary}</p>

      {result.timed_out && (
        <div className="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800">
          ⏱ 시간 상한에 도달해 일부 증거만으로 작성한 보고서입니다.
        </div>
      )}

      {!result.valid && (
        <div className="mt-3 rounded-md bg-rose-50 px-3 py-2 text-xs text-rose-800">
          ⚠ 증거 검증 실패: {result.validation_errors.join(' / ')}
        </div>
      )}

      <h3 className="mt-5 text-xs font-semibold tracking-wide text-slate-500 uppercase">원인 후보</h3>
      {d.root_causes.length === 0 && <p className="mt-2 text-sm text-slate-500">발견된 문제 없음</p>}
      <ol className="mt-2 space-y-3">
        {d.root_causes.map((c, i) => (
          <li key={i} className="rounded-lg border border-slate-100 bg-slate-50 p-3">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold">
                {i + 1}. {c.title}
              </span>
              <span className={`rounded px-1.5 py-0.5 text-xs ${CONFIDENCE_STYLE[c.confidence]}`}>
                {c.confidence}
              </span>
            </div>
            <p className="mt-1 text-sm text-slate-700">{c.explanation}</p>
            <div className="mt-2 flex flex-wrap gap-1">
              {c.evidence_refs.map((ref) => (
                <button
                  key={ref}
                  data-testid="evidence-link"
                  className="rounded border border-sky-200 bg-sky-50 px-2 py-0.5 font-mono text-xs text-sky-800 hover:bg-sky-100"
                  onClick={() => onEvidence(ref)}
                >
                  증거 {labels[ref] ?? ref}
                </button>
              ))}
            </div>
          </li>
        ))}
      </ol>

      {d.next_actions.length > 0 && (
        <>
          <h3 className="mt-5 text-xs font-semibold tracking-wide text-slate-500 uppercase">
            다음 조치 <span className="font-normal normal-case">(제안만 — KubeLens는 실행하지 않음)</span>
          </h3>
          <ul className="mt-2 space-y-2">
            {d.next_actions.map((a, i) => (
              <li key={i} className="text-sm">
                {a.description}
                {a.command && <CommandLine command={a.command} />}
              </li>
            ))}
          </ul>
        </>
      )}

      {result.usage.llm_calls !== undefined && (
        <p className="mt-4 text-xs text-slate-400">
          LLM {result.usage.llm_calls}회 · 입력 {result.usage.input_tokens} / 출력 {result.usage.output_tokens} 토큰
        </p>
      )}
    </div>
  )
}

function CommandLine({ command }: { command: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="mt-1 flex items-center gap-2 rounded-md bg-slate-900 px-3 py-1.5 font-mono text-xs text-slate-100">
      <span className="flex-1 overflow-x-auto whitespace-pre">$ {command}</span>
      <button
        className="text-slate-400 hover:text-white"
        onClick={() => {
          void navigator.clipboard?.writeText(command)
          setCopied(true)
          setTimeout(() => setCopied(false), 1200)
        }}
      >
        {copied ? '복사됨' : '복사'}
      </button>
    </div>
  )
}
