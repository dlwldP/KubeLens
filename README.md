# KubeLens

자연어로 묻는 Kubernetes 클러스터 진단 도구. 조회만 하고, 모든 결론에 근거를 붙인다.

> 현재 단계: **Phase 4 — API** (Phase 0~3 완료)

## 사전 준비

- Docker
- [kind](https://kind.sigs.k8s.io/)
- kubectl
- [uv](https://docs.astral.sh/uv/) (Python 3.12)
- make

## 빠른 시작

```bash
make phase0        # 클러스터 생성 → RBAC → kubeconfig 생성 → 권한 검사 → 장애 시나리오 배포 → 상태 검증
```

단계별로 실행하려면:

```bash
make cluster-up    # kind 클러스터 (control-plane 1 + worker 1)
make rbac          # read-only ServiceAccount + infra/kubelens.kubeconfig 생성
make rbac-check    # 조회는 되고, 쓰기·secret·exec는 막혔는지 확인
make chaos-up      # 장애 시나리오 S1~S6 배포
make chaos-verify  # 각 파드가 의도한 장애 상태인지 확인 (최대 180초 대기)
```

정리:

```bash
make chaos-down
make cluster-down
```

## 백엔드 (Phase 1: 도구 레이어)

`backend/kubelens/tools/`에 기획서 8장의 도구 8종이 있다. 모든 도구는 `ReadOnlyKube`(get/list 메서드만 노출)를 통해 조회만 하고,
출력은 **마스킹 → 4,000자 절단**을 거친 문자열이다.

```bash
make setup       # uv sync (backend/)
make lint        # ruff check + format --check
make test-unit   # 클러스터 없이 도는 단위 테스트 (마스킹·절단·도구 포맷·read-only 가드)
make test-kind   # kind 대상 통합 테스트 (make phase0 선행 필요)
make test        # 위 세 가지 전부
```

도구를 직접 호출해 보기:

```bash
cd backend
uv run python -c "
from kubelens.tools import ReadOnlyKube, run_tool
kube = ReadOnlyKube.from_kubeconfig()   # infra/kubelens.kubeconfig
print(run_tool(kube, 'list_pods', {'namespace': 'kubelens-chaos', 'only_unhealthy': True}).output)
"
```

| 도구 | 비고 |
|---|---|
| `list_namespaces()` | |
| `list_pods(namespace, label_selector=None, only_unhealthy=False)` | `namespace="*"`면 전체 |
| `describe_pod(namespace, name)` | 컨테이너 상태·마지막 종료 사유·리소스·스케줄 조건·최근 이벤트. secret 참조는 이름만 |
| `get_pod_logs(namespace, name, container=None, previous=False, tail=200)` | tail 최대 200 |
| `get_events(namespace, involved_object=None, limit=50)` | 최신순. `involved_object`는 하위 리소스(RS/Pod) 접두어까지 매칭 |
| `list_deployments(namespace)` | |
| `describe_deployment(namespace, name)` | 조건·ReplicaSet·관련 이벤트 |
| `get_nodes()` | 라벨·taint·allocatable(Gi 환산) |

## 에이전트 (Phase 2: CLI)

`backend/kubelens/agent/`의 LangGraph 그래프가 기획서 8장 흐름대로 동작한다.

```
질문 → plan → tool_loop(agent ⇄ tools, 도구 호출 최대 8회) → synthesize → validate → 결과
                                                               └ 증거 없는 원인 후보 → 1회 재생성
```

- 모델: `claude-sonnet-5-5` (`KUBELENS_MODEL`로 변경 가능). 거부(refusal) 시 서버 측 자동 대체 모델 재시도 사용
- 도구 결과에는 `tc_1`, `tc_2` … evidence id가 붙고, 모든 원인 후보는 실제 존재하는 id를 1개 이상 인용해야 검증을 통과한다
- 최종 출력은 `Diagnosis` 스키마(요약 / 원인 후보·신뢰도·근거 / 다음 조치 명령어 제안)로 구조화된다

```bash
export ANTHROPIC_API_KEY=...
make ask Q="kubelens-chaos 네임스페이스의 payment-api가 계속 재시작돼. 왜 그래?"
# 또는
cd backend && uv run python -m kubelens ask "..." [-n NAMESPACE] [--json]
```

종료 코드: `0` 정상, `1` 오류(거부·파싱 실패 등), `2` 증거 검증 실패.

## 평가 하네스 (Phase 3)

`chaos/expected.yaml`의 S1~S6 질문을 실제 에이전트로 진단하고, **1순위 원인 후보**(제목+설명)에 정답 키워드 그룹이
모두 들어 있는지 채점한다. 증거 검증 실패나 비밀값 노출(`must_not_appear`)이 있으면 오답으로 처리한다.

```bash
make eval                  # 전체 (kind + ANTHROPIC_API_KEY 필요)
make eval SCENARIOS=S1,S3  # 일부만
```

결과는 `eval/results/latest.md`(표)와 `latest.json`에 저장된다. 목표(6개 중 5개 이상 적중 + 모든 원인 후보에 증거 링크)를
못 넘으면 종료 코드 1. GitHub Actions에서는 **Actions → CI → Run workflow**로 수동 실행한다(API 비용이 들기 때문에 push 때는 돌지 않음).

## API (Phase 4)

FastAPI + SQLite(SQLModel) + SSE. `make api`로 `http://127.0.0.1:8000`에 띄운다 (`/docs`에 OpenAPI 문서).

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/kube/contexts?kubeconfig_path=` | kubeconfig의 context 이름 목록 (내용은 반환하지 않음) |
| GET/POST | `/api/clusters` | 클러스터 목록 / 등록 |
| POST | `/api/clusters/{id}/test` | 연결 테스트 (네임스페이스 조회 + 서버 버전) |
| GET/POST | `/api/sessions` | 세션 목록 / 생성 |
| GET | `/api/sessions/{id}` | 메시지 + 진단 결과 + 도구 호출 |
| POST | `/api/sessions/{id}/messages` | 질문 전송, **SSE 응답** (`thinking`, `tool_start`, `tool_end`, `final`, `error`) |
| GET | `/api/toolcalls/{id}` | 증거 원본 (마스킹 적용) |

진단 결과의 `evidence_refs`는 DB의 `ToolCall.id`를 가리킨다. SSE 이벤트 순서 확인:

```bash
make api            # 터미널 1
make sse-smoke      # 터미널 2 — curl로 질문을 보내고 이벤트 순서를 검사
```

환경 변수: `KUBELENS_DB_URL`(기본 `backend/kubelens.db`), `KUBELENS_KUBECONFIG`, `KUBELENS_MODEL`.
테스트용 주입: `KUBELENS_LLM_FACTORY=tests.fake_llms:scripted_llms`(API 키 없이 대본대로 움직이는 가짜 LLM),
`KUBELENS_KUBE_FACTORY=tests.fakes:fake_kube`(클러스터 없이). 둘 다 `backend/`에서 실행할 때만 import된다.

## 장애 시나리오

| ID | 시나리오 | 기대 상태 |
|---|---|---|
| S1 | 필수 환경변수 누락으로 종료 | CrashLoopBackOff |
| S2 | 존재하지 않는 이미지 태그 | ErrImagePull / ImagePullBackOff |
| S3 | 메모리 limit 초과 | OOMKilled |
| S4 | 노드 용량을 넘는 메모리 요청 | Pending (Insufficient memory) |
| S5 | 매칭되는 노드가 없는 nodeSelector | Pending (node affinity/selector) |
| S6 | 없는 ConfigMap 참조 | CreateContainerConfigError |

정답 키워드는 `chaos/expected.yaml`에 있고, Phase 3 평가 하네스가 이 파일을 사용한다.

## 문서

- 기획서: [docs/KubeLens_기획서.md](docs/KubeLens_기획서.md)
- AI 작업 규칙: [CLAUDE.md](CLAUDE.md)
