import type { SessionSummary } from '../api'

type Props = {
  sessions: SessionSummary[]
  activeId: number | null
  onSelect: (id: number) => void
  onNew: () => void
}

export function SessionList({ sessions, activeId, onSelect, onNew }: Props) {
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-slate-200 bg-white">
      <div className="p-3">
        <button
          className="w-full rounded-md bg-slate-900 px-3 py-2 text-sm text-white hover:bg-slate-700"
          onClick={onNew}
        >
          + 새 진단
        </button>
      </div>
      <ul className="flex-1 overflow-y-auto px-2 pb-3" data-testid="session-list">
        {sessions.map((s) => (
          <li key={s.id}>
            <button
              className={`w-full truncate rounded-md px-3 py-2 text-left text-sm ${s.id === activeId ? 'bg-slate-100 font-medium' : 'hover:bg-slate-50'}`}
              onClick={() => onSelect(s.id)}
              title={s.title}
            >
              {s.title}
              <span className="block text-xs font-normal text-slate-400">
                {new Date(s.created_at).toLocaleString()}
              </span>
            </button>
          </li>
        ))}
        {sessions.length === 0 && (
          <li className="px-3 py-2 text-sm text-slate-400">진단 기록이 없습니다</li>
        )}
      </ul>
    </aside>
  )
}
