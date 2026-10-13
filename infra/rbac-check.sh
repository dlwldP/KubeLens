#!/usr/bin/env bash
# 앱용 kubeconfig로 "조회는 되고, 쓰기·secret·exec는 막혔는지" 확인한다.
set -uo pipefail

KC="${KC:-infra/kubelens.kubeconfig}"
K="kubectl --kubeconfig ${KC}"
FAIL=0

if [ ! -f "$KC" ]; then
  echo "❌ ${KC} 가 없습니다. 먼저 'make rbac'을 실행하세요." >&2
  exit 1
fi

check() {
  local want="$1"; shift
  local got
  got="$($K auth can-i "$@" 2>/dev/null || true)"
  got="${got%%$'\n'*}"
  if [ "$got" = "$want" ]; then
    printf "  ✅ %-4s %s\n" "$want" "$*"
  else
    printf "  ❌ %-4s %s (실제: %s)\n" "$want" "$*" "${got:-error}"
    FAIL=1
  fi
}

echo "허용되어야 함:"
check yes list pods --all-namespaces
check yes get pods --subresource=log --all-namespaces
check yes list events --all-namespaces
check yes list events.events.k8s.io --all-namespaces
check yes list nodes
check yes list namespaces
check yes list services --all-namespaces
check yes list deployments.apps --all-namespaces
check yes list replicasets.apps --all-namespaces

echo "차단되어야 함:"
check no get secrets --all-namespaces
check no list secrets --all-namespaces
check no get configmaps --all-namespaces
check no create pods --all-namespaces
check no delete pods --all-namespaces
check no create pods --subresource=exec --all-namespaces
check no patch deployments.apps --all-namespaces
check no update deployments.apps --subresource=scale --all-namespaces
check no delete namespaces

echo "실제 조회:"
if $K get pods --all-namespaces >/dev/null 2>&1; then
  echo "  ✅ get pods -A 성공"
else
  echo "  ❌ get pods -A 실패"
  FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  echo "❌ RBAC 검사 실패" >&2
  exit 1
fi
echo "✅ RBAC 검사 통과"
