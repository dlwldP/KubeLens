#!/usr/bin/env bash
# 장애 시나리오 S1~S6 파드가 각각 의도한 장애 상태에 도달했는지 확인한다.
# macOS 기본 bash 3.2에서도 동작하도록 연관 배열을 쓰지 않는다.
# 관리자 context(kind-kubelens)로 조회만 한다.
set -uo pipefail

CTX="${CTX:-kind-kubelens}"
NS="${NS:-kubelens-chaos}"
TIMEOUT="${TIMEOUT:-180}"
K="kubectl --context ${CTX} -n ${NS}"

jp() {  # jp <scenario> <jsonpath>
  $K get pods -l "kubelens.io/scenario=$1" -o jsonpath="$2" 2>/dev/null || true
}

waiting_reason()  { jp "$1" '{.items[0].status.containerStatuses[0].state.waiting.reason}'; }
sched_message()   { jp "$1" '{.items[0].status.conditions[?(@.type=="PodScheduled")].message}'; }
terminated_reason() {
  jp "$1" '{.items[0].status.containerStatuses[0].lastState.terminated.reason} {.items[0].status.containerStatuses[0].state.terminated.reason}'
}

check() {  # check <id> → 0이면 기대 상태 도달
  case "$1" in
    s1) [ "$(waiting_reason s1)" = "CrashLoopBackOff" ] ;;
    s2) [[ "$(waiting_reason s2)" =~ ^(ErrImagePull|ImagePullBackOff)$ ]] ;;
    s3) [[ "$(terminated_reason s3)" == *OOMKilled* ]] ;;
    s4) [[ "$(sched_message s4)" == *"Insufficient memory"* ]] ;;
    s5) [[ "$(sched_message s5)" == *"affinity/selector"* ]] ;;
    s6) [ "$(waiting_reason s6)" = "CreateContainerConfigError" ] ;;
  esac
}

describe() {
  case "$1" in
    s1) echo "CrashLoopBackOff        (현재: $(waiting_reason s1))" ;;
    s2) echo "ImagePullBackOff        (현재: $(waiting_reason s2))" ;;
    s3) echo "OOMKilled               (현재: $(terminated_reason s3 | xargs))" ;;
    s4) echo "Pending/Insufficient    (현재: $(sched_message s4 | cut -c1-60))" ;;
    s5) echo "Pending/NodeSelector    (현재: $(sched_message s5 | cut -c1-60))" ;;
    s6) echo "CreateContainerConfigError (현재: $(waiting_reason s6))" ;;
  esac
}

ALL=(s1 s2 s3 s4 s5 s6)
OK=" "
is_ok() { [[ "$OK" == *" $1 "* ]]; }
up() { printf %s "$1" | tr a-z A-Z; }
START=$(date +%s)

echo "장애 상태 대기 중 (최대 ${TIMEOUT}초)..."
while :; do
  for s in "${ALL[@]}"; do
    is_ok "$s" && continue
    if check "$s"; then
      OK="${OK}${s} "
      echo "  ✅ $(up "$s") 도달 ($(( $(date +%s) - START ))s)"
    fi
  done
  [ "$(echo $OK | wc -w)" -eq "${#ALL[@]}" ] && break
  [ $(( $(date +%s) - START )) -ge "$TIMEOUT" ] && break
  sleep 5
done

echo ""
echo "결과:"
FAIL=0
for s in "${ALL[@]}"; do
  if is_ok "$s"; then
    printf "  ✅ %s  %s\n" "$(up "$s")" "$(describe "$s")"
  else
    printf "  ❌ %s  기대: %s\n" "$(up "$s")" "$(describe "$s")"
    FAIL=1
  fi
done

if [ "$FAIL" -ne 0 ]; then
  echo ""
  echo "❌ 일부 시나리오가 기대 상태에 도달하지 못했습니다. 'make chaos-status'로 확인하세요." >&2
  exit 1
fi
echo "✅ 6개 시나리오 모두 기대 상태"
