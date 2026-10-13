import { useEffect, useState } from 'react'
import { api, type Cluster, type KubeContext } from '../api'

type Props = { onReady: (cluster: Cluster) => void; onCancel?: () => void }

// 화면 1: kubeconfig context 선택 → 연결 테스트 → 저장 (F1)
export function ClusterSetup({ onReady, onCancel }: Props) {
  const [path, setPath] = useState('')
  const [resolvedPath, setResolvedPath] = useState('')
  const [contexts, setContexts] = useState<KubeContext[]>([])
  const [context, setContext] = useState('')
  const [name, setName] = useState('kind-kubelens')
  const [status, setStatus] = useState<{ ok: boolean; message: string } | null>(null)
  const [cluster, setCluster] = useState<Cluster | null>(null)
  const [busy, setBusy] = useState(false)

  function loadContexts(p: string) {
    api.contexts(p || undefined).then(
      (res) => {
        setResolvedPath(res.kubeconfig_path)
        setContexts(res.contexts)
        setContext(res.contexts.find((c) => c.current)?.name ?? res.contexts[0]?.name ?? '')
      },
      (e: Error) => {
        setContexts([])
        setStatus({ ok: false, message: e.message })
      },
    )
  }

  useEffect(() => loadContexts(''), [])

  async function test() {
    setBusy(true)
    try {
      const created = await api.createCluster({
        name,
        kubeconfig_path: resolvedPath || undefined,
        context: context || undefined,
      })
      const res = await api.testCluster(created.id)
      setStatus({
        ok: res.ok,
        message: res.ok ? `${res.message} · 서버 버전 ${res.server_version}` : res.message,
      })
      setCluster(res.ok ? { ...created, server_version: res.server_version ?? null } : null)
    } catch (e) {
      setStatus({ ok: false, message: (e as Error).message })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto mt-16 max-w-xl rounded-xl border border-slate-200 bg-white p-8 shadow-sm">
      <h1 className="text-xl font-semibold">클러스터 설정</h1>
      <p className="mt-1 text-sm text-slate-500">
        KubeLens는 조회만 합니다. read-only ServiceAccount kubeconfig를 쓰세요.
      </p>

      <label className="mt-6 block text-sm font-medium">kubeconfig 경로</label>
      <div className="mt-1 flex gap-2">
        <input
          className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm"
          placeholder={resolvedPath || 'infra/kubelens.kubeconfig (기본값)'}
          value={path}
          onChange={(e) => setPath(e.target.value)}
        />
        <button
          className="rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
          onClick={() => {
            setStatus(null)
            loadContexts(path)
          }}
        >
          불러오기
        </button>
      </div>

      <label className="mt-4 block text-sm font-medium">context</label>
      <select
        data-testid="context-select"
        className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        value={context}
        onChange={(e) => setContext(e.target.value)}
      >
        {contexts.map((c) => (
          <option key={c.name} value={c.name}>
            {c.name}
            {c.current ? ' (current)' : ''}
          </option>
        ))}
      </select>

      <label className="mt-4 block text-sm font-medium">이름</label>
      <input
        className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />

      {status && (
        <p
          data-testid="connection-status"
          className={`mt-4 rounded-md px-3 py-2 text-sm ${status.ok ? 'bg-emerald-50 text-emerald-800' : 'bg-rose-50 text-rose-800'}`}
        >
          {status.ok ? '✔ ' : '✖ '}
          {status.message}
        </p>
      )}

      <div className="mt-6 flex justify-end gap-2">
        {onCancel && (
          <button className="rounded-md px-4 py-2 text-sm text-slate-600" onClick={onCancel}>
            취소
          </button>
        )}
        <button
          className="rounded-md border border-slate-300 px-4 py-2 text-sm hover:bg-slate-50 disabled:opacity-50"
          disabled={busy || !name}
          onClick={() => void test()}
        >
          {busy ? '테스트 중…' : '연결 테스트'}
        </button>
        <button
          className="rounded-md bg-slate-900 px-4 py-2 text-sm text-white disabled:opacity-40"
          disabled={!cluster}
          onClick={() => cluster && onReady(cluster)}
        >
          저장하고 시작
        </button>
      </div>
    </div>
  )
}
