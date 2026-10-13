#!/usr/bin/env bash
# Phase 4 완료 조건: curl로 SSE 이벤트 순서 확인.
# 실행 중인 API(기본 http://127.0.0.1:8000)에 클러스터·세션을 만들고 질문을 보내 이벤트 순서를 검사한다.
set -euo pipefail

API="${API:-http://127.0.0.1:8000}"
QUESTION="${QUESTION:-지금 클러스터에 문제 있는 거 있어?}"
OUT="$(mktemp)"

json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

CLUSTER_ID=$(curl -fsS -X POST "$API/api/clusters" -H 'content-type: application/json' \
  -d '{"name":"smoke"}' | json '["id"]')
echo "cluster: $(curl -fsS -X POST "$API/api/clusters/$CLUSTER_ID/test")"
SESSION_ID=$(curl -fsS -X POST "$API/api/sessions" -H 'content-type: application/json' \
  -d "{\"cluster_id\": $CLUSTER_ID}" | json '["id"]')

echo "--- SSE ---"
curl -fsSN -X POST "$API/api/sessions/$SESSION_ID/messages" -H 'content-type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1]}))' "$QUESTION")" \
  | tee "$OUT" | grep --line-buffered '^event:'

python3 - "$OUT" <<'PY'
import json, sys
blocks = [b for b in open(sys.argv[1]).read().strip().split("\n\n") if b]
events = [dict(l.split(": ", 1) for l in b.splitlines()) for b in blocks]
types = [e["event"] for e in events]
assert types[0] == "thinking", types
assert types[-1] == "final", types
assert types.count("tool_start") >= 1 and types.count("tool_start") == types.count("tool_end"), types
open_calls = 0
for t in types:
    open_calls += {"tool_start": 1, "tool_end": -1}.get(t, 0)
    assert open_calls >= 0, types
final = json.loads(events[-1]["data"])
ids = {json.loads(e["data"])["id"] for e in events if e["event"] == "tool_end"}
for cause in final["diagnosis"]["root_causes"]:
    assert cause["evidence_refs"] and set(cause["evidence_refs"]) <= ids, cause
print(f"✅ SSE 이벤트 순서 OK: {' → '.join(types)}")
PY
