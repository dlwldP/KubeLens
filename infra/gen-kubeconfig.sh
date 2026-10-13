#!/usr/bin/env bash
# read-only ServiceAccount 토큰으로 앱 전용 kubeconfig를 만든다.
# 출력 파일은 .gitignore 대상이며 LLM에 절대 전달하지 않는다.
set -euo pipefail

CTX="${CTX:-kind-kubelens}"
OUT="${OUT:-infra/kubelens.kubeconfig}"
NS="kubelens-system"
SECRET="kubelens-reader-token"
K="kubectl --context ${CTX}"

TOKEN_B64=""
for _ in $(seq 1 30); do
  TOKEN_B64="$($K -n "$NS" get secret "$SECRET" -o jsonpath='{.data.token}' 2>/dev/null || true)"
  [ -n "$TOKEN_B64" ] && break
  sleep 1
done
if [ -z "$TOKEN_B64" ]; then
  echo "❌ ServiceAccount 토큰이 생성되지 않았습니다 ($NS/$SECRET)" >&2
  exit 1
fi
TOKEN="$(printf '%s' "$TOKEN_B64" | base64 --decode)"

CLUSTER_NAME="$(kubectl config view -o jsonpath="{.contexts[?(@.name==\"${CTX}\")].context.cluster}")"
SERVER="$(kubectl config view -o jsonpath="{.clusters[?(@.name==\"${CLUSTER_NAME}\")].cluster.server}")"
CA="$(kubectl config view --raw -o jsonpath="{.clusters[?(@.name==\"${CLUSTER_NAME}\")].cluster.certificate-authority-data}")"

if [ -z "$SERVER" ] || [ -z "$CA" ]; then
  echo "❌ context '${CTX}'의 클러스터 정보를 찾을 수 없습니다" >&2
  exit 1
fi

umask 077
cat > "$OUT" <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: kubelens
    cluster:
      server: ${SERVER}
      certificate-authority-data: ${CA}
users:
  - name: kubelens-reader
    user:
      token: ${TOKEN}
contexts:
  - name: kubelens-readonly
    context:
      cluster: kubelens
      user: kubelens-reader
current-context: kubelens-readonly
EOF

echo "✅ ${OUT} 생성 (context: kubelens-readonly)"
