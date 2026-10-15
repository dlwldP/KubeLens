# KubeLens

[![CI](https://github.com/dlwldP/KubeLens/actions/workflows/ci.yml/badge.svg)](https://github.com/dlwldP/KubeLens/actions/workflows/ci.yml)

> 자연어로 묻는 Kubernetes 클러스터 진단 도구. **조회만 하고**, 모든 결론에 **근거(실제 조회 결과)** 를 붙인다.

"왜 이 파드가 죽어?"라고 물으면 에이전트가 read-only로 events·logs·describe를 수집하고,
**증거 링크가 붙은 원인 후보**와 **다음 조치 명령어(제안만)** 를 보여준다.

![demo](docs/demo.gif)

<sub>데모 GIF가 보이지 않으면 아직 생성 전이다 — [데모 GIF 만들기](#데모-gif-만들기) 참고.</sub>

## 특징

- **Read-only by construction**: RBAC(쓰기·secret·exec 권한 없음)와 코드(get/list만 노출하는 `ReadOnlyKube`, 8개 도구 allowlist) 두 겹으로 막는다. 쓰기 API 호출·셸 실행·secret 접근이 소스에 없는지 테스트가 AST로 검사한다.
- **증거 없는 주장 금지**: 도구 호출마다 ID가 붙고, 모든 원인 후보는 실제 존재하는 호출 ID를 1개 이상 인용해야 한다. 아니면 1회 재생성, 그래도 안 되면 실패로 표시한다.
- **정확도를 수치로 측정**: kind에 재현한 장애 6종(S1~S6)을 자동으로 진단·채점하는 평가 하네스(`make eval`).
- **비용 상한**: 질문당 도구 호출 최대 8회, 로그 tail 200줄, 도구 출력 4,000자 절단.
- **민감정보 마스킹**: Bearer/Basic 인증, `password/token/secret/api_key/credentials=…`, JWT, PEM 개인키, URL 속 비밀번호(`postgres://user:***@host`), AWS·GitHub·Anthropic·Slack 토큰을 LLM 전달 전에 `***`로 가린다. 무작위 입력 기반 속성 테스트(hypothesis)로 검증한다. kubeconfig는 LLM에 보내지 않는다.
- **견고성**: 증거 수집 시간 상한(기본 180초, 넘으면 모은 증거로 보고서 작성), 브라우저 연결이 끊기면 진단 중단, 동시 질문 격리(SQLite WAL), kubeconfig는 허용 디렉터리 안의 경로만 받는다.

## 빠른 시작 (약 10분)

**사전 준비**: Docker, [kind](https://kind.sigs.k8s.io/), kubectl, [uv](https://docs.astral.sh/uv/), Node.js 22+, make, Anthropic API 키

```bash
git clone https://github.com/dlwldP/KubeLens.git && cd KubeLens

make phase0                       # kind 클러스터 + read-only RBAC + 장애 시나리오 S1~S6 배포·검증 (2~3분)
make setup                        # 백엔드 의존성 설치 + UI 빌드

export ANTHROPIC_API_KEY=sk-ant-...
make api                          # http://127.0.0.1:8000 (UI 포함)
```

브라우저에서 http://127.0.0.1:8000 을 열고

1. **클러스터 설정**: context `kubelens-readonly` 선택 → **연결 테스트** → **저장하고 시작**
2. 질문 예: `kubelens-chaos 네임스페이스의 payment-api가 계속 재시작돼. 왜 그래?`
3. 오른쪽 트레이스 패널에 도구 호출이 실시간으로 쌓이고, 진단 카드의 **증거** 버튼을 누르면 원본 출력(마스킹 적용)이 열린다.

### Docker로 실행 (Linux)

```bash
make phase0
make docker-build
ANTHROPIC_API_KEY=sk-ant-... make docker-run   # http://127.0.0.1:8000
```

kind의 API 서버가 호스트의 127.0.0.1에 있으므로 `--network host`로 실행하고, 서버는 127.0.0.1에만 바인딩한다.
kubeconfig(`infra/kubelens.kubeconfig`)는 이미지에 넣지 않고 읽기 전용으로 마운트한다.
macOS의 Docker Desktop은 host 네트워크 동작이 달라 `make api` 실행을 권장한다.

### CLI

CLI로도 쓸 수 있다.

```bash
make ask Q="batch-trainer가 계속 Pending이야. 왜 스케줄이 안 돼?"
```

정리: `make chaos-down` / `make cluster-down`

## 아키텍처

```
[React UI] ──HTTP/SSE──▶ [FastAPI] ──▶ [SQLite: 클러스터 / 세션 / 메시지 / 도구 호출]
                             │
                             ▼
                    [LangGraph 에이전트]   plan → tool_loop(최대 8회) → synthesize → validate
                             │                                          └ 증거 없음 → 1회 재생성
                             ▼  allowlist 8개 도구만
                    [도구 레이어]  ReadOnlyKube(get/list만) → 마스킹 → 4,000자 절단
                             │  read-only ServiceAccount (infra/kubelens.kubeconfig)
                             ▼
                       [K8s 클러스터]
```

| 영역 | 구성 |
|---|---|
| 에이전트 | Python 3.12, LangGraph, langchain-anthropic, `claude-sonnet-5-5` (`KUBELENS_MODEL`로 변경) |
| 백엔드 | FastAPI + SSE, SQLModel + SQLite |
| K8s 접근 | `kubernetes` Python client (kubectl 셸 실행 없음) |
| 프런트 | React + Vite + TypeScript strict + Tailwind |
| 테스트 | pytest(단위·kind 통합), Playwright e2e, GitHub Actions + kind |

### 도구 (기획서 8장, 이 외에는 없음)

| 도구 | 비고 |
|---|---|
| `list_namespaces()` | |
| `list_pods(namespace, label_selector=None, only_unhealthy=False)` | `namespace="*"`면 전체 |
| `describe_pod(namespace, name)` | 컨테이너 상태·마지막 종료 사유·리소스·스케줄 조건·최근 이벤트. secret 참조는 이름만 |
| `get_pod_logs(namespace, name, container=None, previous=False, tail=200)` | tail 최대 200 |
| `get_events(namespace, involved_object=None, limit=50)` | 최신순, 하위 리소스(RS/Pod) 접두어까지 매칭 |
| `list_deployments(namespace)` | |
| `describe_deployment(namespace, name)` | 조건·ReplicaSet·관련 이벤트 |
| `get_nodes()` | 라벨·taint·allocatable(Gi 환산) |

### Read-only 방어선

| 층 | 무엇을 막나 | 검증 |
|---|---|---|
| RBAC `kubelens-readonly` | 쓰기 동사, secrets, configmaps, pods/exec | `make rbac-check` (can-i 18개 항목), kind 테스트에서 secret 조회 403 확인 |
| `ReadOnlyKube` | get/list 메서드만 정의, 원본 client 우회 경로 없음(`__slots__`) | `tests/test_readonly_guard.py` |
| 도구 allowlist | LLM은 8개 도구만 호출 가능, 그 외 이름은 거부 | `run_tool` 단위 테스트 |
| 소스 검사 | `create_/delete_/patch_/replace_/connect_` 호출, `subprocess`·`kubernetes.stream` import 금지 | AST 가드 테스트 |
| 마스킹 | 도구 출력의 토큰·키 패턴 | `tests/test_masking.py`, S1 로그의 가짜 키 노출 여부를 평가에서도 검사 |

## 평가

`chaos/expected.yaml`의 질문으로 S1~S6을 진단하고, **1순위 원인 후보**(제목+설명)에 정답 키워드 그룹이 모두 있는지 채점한다.
증거 검증 실패나 비밀값 노출이 있으면 오답이다. 목표: 6개 중 5개 이상 적중 + 모든 원인 후보에 증거 링크.

| # | 시나리오 | 재현 방법 | 정답 키워드 |
|---|---|---|---|
| S1 | CrashLoopBackOff | 필수 환경변수 `DB_HOST` 누락으로 exit 1 | `DB_HOST` + 환경변수 |
| S2 | ImagePullBackOff | 존재하지 않는 이미지 태그 | image + 태그/not found/pull |
| S3 | OOMKilled | memory limit 32Mi에서 150M 할당 | OOMKilled + memory limit |
| S4 | Pending (리소스 부족) | requests.memory 64Gi | Insufficient memory + 요청량 |
| S5 | Pending (스케줄 불가) | 없는 노드 풀 nodeSelector | nodeSelector/affinity + `kubelens.io/pool` |
| S6 | CreateContainerConfigError | 없는 ConfigMap 참조 | ConfigMap + `order-service-config` |

```bash
make eval                   # 전체 (kind + ANTHROPIC_API_KEY 필요, 1회 약 1달러 내외)
make eval SCENARIOS=S1,S3   # 일부만
```

결과는 `eval/results/latest.md`(표)와 `latest.json`에 저장된다.
GitHub Actions에서는 **Actions → CI → Run workflow**로 수동 실행하며(push 때는 API 비용 때문에 돌지 않음), 결과 표가 실행 요약에 붙는다.

### 결과

<!-- make eval 후 eval/results/latest.md의 표를 여기에 붙여 넣는다 -->
_아직 실제 LLM으로 평가를 실행하지 않았다._

## 개발

```bash
make lint          # ruff + oxlint + tsc
make test-unit     # 백엔드 단위·API·동시성 테스트 + 평가 채점 테스트 + 프런트 Vitest (클러스터·API 키 불필요)
make test-kind     # kind 통합 테스트 (make phase0 선행)
make api           # API + UI 서버
make ui-dev        # 프런트 개발 서버 http://localhost:5173 (/api 프록시)
make sse-smoke     # 실행 중인 API에 질문을 보내 SSE 이벤트 순서 검사
make e2e           # 실행 중인 서버 대상 Playwright e2e
```

API 키나 클러스터 없이 배선을 확인할 때는 가짜 구현을 주입할 수 있다 (`backend/`에서 실행할 때만 import됨).

```bash
cd backend
KUBELENS_LLM_FACTORY=tests.fake_llms:scripted_llms \  # 대본대로 움직이는 가짜 LLM
KUBELENS_KUBE_FACTORY=tests.fakes:fake_kube \         # 가짜 클러스터
  uv run uvicorn kubelens.api.app:app
```

### CI

| 잡 | 언제 | 내용 |
|---|---|---|
| lint + unit tests | push / PR | ruff, oxlint, tsc, shellcheck, pytest, Vitest |
| macOS lint + unit tests | push / PR | macOS에서 `make setup`·lint·단위 테스트, 기본 bash 3.2로 스크립트 문법 확인 |
| phase0 + kind integration | push / PR | kind 생성 → RBAC 검사 → S1~S6 상태 검증 → kind 통합 테스트 → SSE 순서 검사 → Playwright e2e (대본 LLM, 실제 클러스터) → Docker 이미지 빌드·연결 테스트 |
| eval | 수동 (Run workflow) | 실제 LLM으로 S1~S6 평가, 결과 표를 Step Summary와 artifact로 |

### API

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/kube/contexts?kubeconfig_path=` | kubeconfig의 context 이름 목록 (내용은 반환하지 않음) |
| GET/POST | `/api/clusters` | 클러스터 목록 / 등록 |
| POST | `/api/clusters/{id}/test` | 연결 테스트 (네임스페이스 조회 + 서버 버전) |
| GET/POST | `/api/sessions` | 세션 목록 / 생성 |
| GET | `/api/sessions/{id}` | 메시지 + 진단 결과 + 도구 호출 |
| POST | `/api/sessions/{id}/messages` | 질문 전송, SSE 응답 (`thinking` · `tool_start` · `tool_end` · `final` · `error`) |
| GET | `/api/toolcalls/{id}` | 증거 원본 (마스킹 적용) |

진단 결과의 `evidence_refs`는 `ToolCall.id`를 가리킨다.

| 환경 변수 | 기본값 | 설명 |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Claude API 키 |
| `KUBELENS_MODEL` | `claude-sonnet-5-5` | 진단 모델 |
| `KUBELENS_KUBECONFIG` | `infra/kubelens.kubeconfig` | 기본 kubeconfig |
| `KUBELENS_KUBECONFIG_DIRS` | 기본 kubeconfig 폴더, `~/.kube` | API가 받을 수 있는 kubeconfig 위치 (`:`로 구분) |
| `KUBELENS_DIAGNOSIS_TIMEOUT` | `180` | 증거 수집 시간 상한(초), `0`이면 무제한 |
| `KUBELENS_DB_URL` | `sqlite:///backend/kubelens.db` | DB 위치 |

### 데모 GIF 만들기

`make api`(실제 API 키)가 떠 있는 상태에서:

```bash
make demo-gif      # S1 질문 과정을 녹화해 docs/demo.gif 생성 (ffmpeg 필요)
```

## 디렉터리

```
backend/kubelens/tools/   도구 레이어 (ReadOnlyKube, 8개 도구, 마스킹, 절단)
backend/kubelens/agent/   LangGraph 에이전트, 출력 스키마, 프롬프트
backend/kubelens/api/     FastAPI, SQLite 모델
backend/tests/            단위·API·kind 통합 테스트, 가짜 클러스터/LLM
eval/                     평가 하네스, 결과(eval/results/)
frontend/                 React UI, Playwright e2e
chaos/                    장애 시나리오 매니페스트, expected.yaml(평가 정답지)
infra/                    kind 설정, RBAC, kubeconfig 생성, 권한 검사, 데모 GIF 스크립트
docs/                     기획서
```

## 하지 않는 것 (Non-goals)

- `apply / delete / exec / scale` 등 **클러스터 상태를 바꾸는 모든 동작** — 다음 조치는 명령어를 제안만 한다
- Secret 조회
- 사용자 인증·멀티테넌시 (로컬 단일 사용자 전제, 서버는 127.0.0.1에만 바인딩)

## 문서

- 기획서: [docs/KubeLens_기획서.md](docs/KubeLens_기획서.md)
- AI 작업 규칙: [CLAUDE.md](CLAUDE.md)
