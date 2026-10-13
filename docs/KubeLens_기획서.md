# KubeLens 기획서

> 자연어로 묻는 Kubernetes 클러스터 진단 도구
> 버전: v0.1 (MVP 기획) · 작성: 2026-10-13

---

## 1. 한 줄 정의

"왜 이 파드가 죽어?"라고 물으면, 에이전트가 **read-only**로 클러스터 상태(events, logs, describe)를 수집하고 **근거가 붙은 원인 후보와 다음 조치**를 보여준다.

## 2. 문제와 타깃

| 항목 | 내용 |
|---|---|
| 문제 | 장애가 나면 `get` → `describe` → `logs` → `events`를 오가며 단서를 수동으로 이어 붙여야 한다. 주니어일수록 어디부터 볼지 모른다. |
| 타깃 | K8s를 쓰는 주니어 개발자·운영자, 홈랩 운영자 |
| 차별점 | ① 모든 결론에 **실제 조회 결과(증거)를 링크** ② **실행 권한 없음**(조회만) ③ 정해진 장애 시나리오 세트로 **정확도를 수치로 측정** |

## 3. 범위

### MVP에 포함
- 클러스터 1개 등록(kubeconfig context 선택) 및 연결 테스트
- 진단 채팅: 질문 → 도구 호출 과정 실시간 표시 → 구조화된 진단 결과
- 증거 뷰어: 각 결론이 참조한 원본 출력 보기
- 세션 히스토리 저장
- 장애 시나리오 6종 + 자동 평가 스크립트

### MVP 이후
- 멀티 클러스터, Prometheus 메트릭 연동, Helm 릴리스 분석, 진단 리포트 공유 링크

### 절대 하지 않을 것 (Non-goals)
- `kubectl apply / delete / exec / scale` 등 **상태를 바꾸는 모든 동작**
- Secret 조회
- 사용자 인증·멀티테넌시 (로컬 단일 사용자 전제)

> 바이브코딩 중 AI가 "편의상" 쓰기 기능을 추가하려 하면 이 섹션을 근거로 거절한다.

## 4. 설계 원칙

1. **Read-only by construction**: 권한(RBAC)과 코드(도구 allowlist) 두 겹으로 막는다.
2. **증거 없는 주장 금지**: 진단 결과의 모든 원인 후보는 최소 1개의 `evidence_ref`(ToolCall ID)를 가져야 하며, 없으면 출력 검증에서 실패 처리한다.
3. **비용 상한**: 질문당 도구 호출 최대 8회, 로그는 tail 200줄, 도구 결과는 LLM에 넣기 전에 요약·절단한다.
4. **민감정보 마스킹**: 로그·env 출력에서 토큰/키 패턴을 마스킹한 뒤 LLM에 전달한다. kubeconfig는 절대 LLM에 넘기지 않는다.

## 5. 사용자 시나리오

1. **CrashLoop 원인 찾기**: "payment 네임스페이스 api 파드가 계속 재시작돼" → events·이전 컨테이너 로그 조회 → "환경변수 `DB_HOST` 누락으로 시작 직후 종료" + 근거 로그 라인
2. **Pending 원인 찾기**: "새로 띄운 worker가 안 떠" → describe·노드 상태 조회 → "요청 메모리 8Gi가 모든 노드의 allocatable 초과"
3. **전체 헬스 체크**: "지금 클러스터에 문제 있는 거 있어?" → 비정상 파드 목록 → 원인별 그룹핑

## 6. 기능 명세

| ID | 기능 | 상세 | 완료 조건 |
|---|---|---|---|
| F1 | 클러스터 등록 | kubeconfig 경로 + context 선택, 연결 테스트(`list namespaces`) | 성공/실패 메시지와 서버 버전 표시 |
| F2 | 진단 질문 | 자연어 입력, 네임스페이스 선택(선택사항) | SSE로 진행 상황 스트리밍 |
| F3 | 도구 트레이스 | 호출한 도구, 인자, 소요시간, 성공 여부를 실시간 표시 | 답변 완료 전에도 트레이스가 보임 |
| F4 | 진단 결과 카드 | 요약 / 원인 후보(신뢰도) / 다음 조치(실행은 안 하고 명령어만 제시) | 원인 후보마다 증거 링크 존재 |
| F5 | 증거 뷰어 | 증거 링크 클릭 → 원본 출력 모달 | 마스킹 적용된 원본 표시 |
| F6 | 세션 히스토리 | 세션 목록, 재열람 | 새로고침 후에도 유지 |
| F7 | 평가 하네스 | 시나리오별 질문 실행 → 정답 키워드 매칭 → 정확도 리포트 | CLI 한 줄로 실행, 결과 표 출력 |

## 7. 아키텍처

```
[React UI] ──HTTP/SSE──▶ [FastAPI]
                            │
                            ▼
                    [LangGraph Agent]
                            │  (allowlist된 도구만)
                            ▼
                    [Tool Layer: kubernetes-python client]
                            │  (read-only ServiceAccount)
                            ▼
                      [K8s Cluster]

[SQLite] ◀── 세션 / 메시지 / 도구 호출 기록
```

### 기술 스택

| 영역 | 선택 | 이유 |
|---|---|---|
| 에이전트 | Python, LangGraph, langchain-anthropic | 도구 루프·상태 관리가 명확함 |
| LLM | Claude Sonnet 5.5 (`claude-sonnet-5-5`) | 도구 호출 안정성, 비용 대비 성능 |
| 백엔드 | FastAPI + SSE, SQLModel + SQLite | 에이전트와 같은 언어, 설정 최소 |
| K8s 접근 | `kubernetes` Python client | kubectl 셸 실행 없이 타입 있는 API |
| 프론트 | React + Vite + TypeScript + Tailwind | 익숙한 스택 |
| 테스트 클러스터 | kind | 로컬에서 장애 재현이 쉬움 |

## 8. 에이전트 설계

### 도구 목록 (이 외에는 존재하지 않음)

| 도구 | 시그니처 | 비고 |
|---|---|---|
| `list_namespaces` | `()` | |
| `list_pods` | `(namespace, label_selector=None, only_unhealthy=False)` | 상태·재시작 횟수 포함 |
| `describe_pod` | `(namespace, name)` | 컨테이너 상태, 마지막 종료 사유, 리소스 요청 |
| `get_pod_logs` | `(namespace, name, container=None, previous=False, tail=200)` | 마스킹 적용 |
| `get_events` | `(namespace, involved_object=None, limit=50)` | 최신순 |
| `list_deployments` | `(namespace)` | desired/ready 수 |
| `describe_deployment` | `(namespace, name)` | 롤아웃 상태, 이미지 |
| `get_nodes` | `()` | 상태, allocatable, taint |

### 그래프 흐름

```
질문 → [plan] → [tool_loop (최대 8회)] → [synthesize] → [validate] → 결과
                                                           │
                                       증거 없는 주장 있음 → 1회 재생성
```

### 출력 스키마 (Pydantic)

```json
{
  "summary": "string",
  "root_causes": [
    {
      "title": "string",
      "confidence": "high | medium | low",
      "explanation": "string",
      "evidence_refs": ["toolcall_id"]
    }
  ],
  "next_actions": [
    { "description": "string", "command": "kubectl ... (제안만, 실행 안 함)" }
  ]
}
```

## 9. 데이터 모델

| 엔티티 | 필드 |
|---|---|
| Cluster | id, name, kubeconfig_path, context, server_version, created_at |
| Session | id, cluster_id, title, created_at |
| Message | id, session_id, role(user/assistant), content, diagnosis_json(nullable), created_at |
| ToolCall | id, message_id, tool_name, args_json, output_text(마스킹 후), truncated(bool), duration_ms, status, created_at |

## 10. API

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/kube/contexts` | 로컬 kubeconfig의 context 목록 |
| POST | `/api/clusters` | 클러스터 등록 |
| POST | `/api/clusters/{id}/test` | 연결 테스트 |
| GET | `/api/sessions` | 세션 목록 |
| POST | `/api/sessions` | 세션 생성 |
| GET | `/api/sessions/{id}` | 메시지 + 도구 호출 포함 조회 |
| POST | `/api/sessions/{id}/messages` | 질문 전송, **SSE 응답** |
| GET | `/api/toolcalls/{id}` | 증거 원본 조회 |

**SSE 이벤트 타입**: `tool_start` · `tool_end` · `thinking`(진행 문구) · `final`(진단 JSON) · `error`

## 11. 화면 구성

1. **클러스터 설정**: context 드롭다운 → 연결 테스트 → 저장
2. **진단 화면 (메인)**: 3단 레이아웃
   - 좌: 세션 목록
   - 중: 대화 + 진단 결과 카드
   - 우: 도구 트레이스 패널(실시간)
3. **증거 모달**: 도구명·인자·원본 출력(코드 블록), 절단 여부 표시

## 12. 보안

**RBAC** (전용 ServiceAccount에 바인딩):

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kubelens-readonly
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "events", "nodes", "namespaces", "services"]
    verbs: ["get", "list"]
  - apiGroups: ["apps"]
    resources: ["deployments", "replicasets"]
    verbs: ["get", "list"]
# secrets, configmaps 데이터, exec는 의도적으로 제외
```

**마스킹 패턴**: `Bearer\s+\S+`, `(password|token|secret|api[_-]?key)\s*[=:]\s*\S+`, JWT 형식(`eyJ...`), AWS 키(`AKIA[0-9A-Z]{16}`)

## 13. 검증용 장애 시나리오

`chaos/` 폴더에 매니페스트로 두고 kind에 적용한다. 이 표가 곧 평가 정답지다.

| # | 시나리오 | 재현 방법 | 정답 키워드 |
|---|---|---|---|
| S1 | CrashLoopBackOff | `command: ["sh","-c","echo missing DB_HOST; exit 1"]` | `DB_HOST`, `exit code 1` |
| S2 | ImagePullBackOff | 존재하지 않는 이미지 태그 | `image`, `not found`/`pull` |
| S3 | OOMKilled | memory limit 32Mi + 메모리 점유 프로세스 | `OOMKilled`, `memory limit` |
| S4 | Pending (리소스 부족) | requests.memory 64Gi | `Insufficient memory` |
| S5 | Pending (스케줄 불가) | 존재하지 않는 nodeSelector | `nodeSelector`/`affinity` |
| S6 | CreateContainerConfigError | 없는 ConfigMap 참조 | `configmap`, 해당 이름 |

**목표**: 6개 중 5개 이상에서 1순위 원인 후보 적중, 모든 원인 후보에 증거 링크 존재.

## 14. 개발 단계 (바이브코딩 Phase)

각 Phase는 **하나의 세션에서 끝내고, 완료 조건을 통과해야 다음으로 간다.**

| Phase | 목표 | 완료 조건 |
|---|---|---|
| 0. 환경 | 모노레포 구조, kind 클러스터, RBAC, chaos 매니페스트 6종 | `make chaos-up` 후 6개 파드가 각각 의도한 상태 |
| 1. 도구 레이어 | 8개 도구 + 마스킹 + 출력 절단 | kind 대상 pytest 통과, 마스킹 단위 테스트 통과 |
| 2. 에이전트 (CLI) | LangGraph 루프 + 출력 스키마 + 검증 | `python -m kubelens ask "..."`로 S1 진단 성공 |
| 3. 평가 하네스 | S1~S6 자동 실행 + 정확도 표 | `make eval`로 결과 표 출력 (이후 모든 변경 후 실행) |
| 4. API | FastAPI + SQLite + SSE | curl로 SSE 이벤트 순서 확인 |
| 5. UI | 3개 화면 + 실시간 트레이스 + 증거 모달 | 브라우저에서 시나리오 S1~S3 수동 확인 |
| 6. 마감 | README, 데모 GIF, 평가 결과 표 | README만 보고 10분 안에 로컬 실행 가능 |

> 핵심: **Phase 3(평가)을 UI보다 먼저** 만든다. 이후 프롬프트나 도구를 바꿀 때마다 정확도가 떨어졌는지 바로 알 수 있어서, 바이브코딩의 "고쳤더니 다른 게 망가짐" 문제를 막아준다.

### Phase별 첫 프롬프트 예시

- **Phase 1**: "CLAUDE.md와 기획서 8장을 읽고, `backend/kubelens/tools/`에 도구 8개를 구현해. kubernetes Python client만 쓰고, 쓰기 API는 import하지 마. 각 도구는 마스킹과 4,000자 절단을 거친 문자열을 반환해. kind 클러스터 대상 pytest도 같이 작성해."
- **Phase 2**: "기획서 8장 그래프 흐름대로 LangGraph 에이전트를 만들어. 도구 호출 최대 8회, 출력은 Pydantic 스키마로 검증하고, evidence_refs가 비어 있는 원인 후보가 있으면 1회 재생성해."

## 15. CLAUDE.md 초안

```markdown
# KubeLens 규칙
- 기획서: docs/KubeLens_기획서.md 가 단일 진실 공급원이다.
- 절대 금지: 클러스터 상태를 변경하는 코드, secrets 조회, kubectl 셸 실행.
- 도구 추가·변경은 사람이 기획서를 먼저 고친 뒤에만 한다.
- 도구/프롬프트를 수정했다면 반드시 `make eval`을 실행하고 결과를 보고한다.
- 한 번에 한 Phase만 작업한다. 완료 조건을 확인한 뒤 멈춘다.
- 백엔드: Python 3.12, ruff, pytest. 프론트: TS strict.
```

## 16. 리스크와 대응

| 리스크 | 대응 |
|---|---|
| LLM이 근거 없이 그럴듯한 답을 만듦 | 증거 링크 필수 검증 + 평가 하네스 |
| 로그가 커서 토큰 비용 폭증 | tail 200, 4,000자 절단, 질문당 호출 8회 상한 |
| 민감정보 유출 | RBAC에서 secrets 제외 + 마스킹 + kubeconfig 비전송 |
| 범위 확장(쓰기 기능, 멀티 클러스터) | Non-goals 명시, CLAUDE.md 금지 규칙 |
| kind 환경과 실 클러스터 차이 | MVP는 kind 기준으로 고정, 이후 실 클러스터 테스트 |

## 17. 포트폴리오 포인트

- 평가 결과 표 (시나리오별 적중 여부, 평균 도구 호출 수, 평균 응답 시간, 질문당 비용)
- "read-only by construction" 보안 설계 설명
- 데모 GIF: 질문 → 실시간 트레이스 → 증거 클릭
