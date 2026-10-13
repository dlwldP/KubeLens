# KubeLens 작업 규칙

## 단일 진실 공급원
- 기획서: `docs/KubeLens_기획서.md`. 구현이 기획서와 다르면 기획서를 따른다.
- 기획서에 없는 기능은 만들지 않는다. 필요해 보이면 제안만 하고 멈춘다.

## 절대 금지
- 클러스터 상태를 변경하는 코드 (create / update / patch / delete / exec / scale)
- Secret 조회, kubeconfig 내용을 LLM 프롬프트에 포함하는 것
- `kubectl`을 subprocess로 실행하는 것 (도구 레이어는 kubernetes Python client만 사용)
- 도구 allowlist(기획서 8장) 변경. 변경은 사람이 기획서를 먼저 수정한 뒤에만 한다.

## 클러스터
- 개발·테스트는 반드시 kind 클러스터(`kind-kubelens` context)만 대상으로 한다.
- 모든 kubectl/Makefile 명령은 `--context kind-kubelens`를 명시한다. 다른 context를 건드리지 않는다.
- 앱이 사용하는 kubeconfig는 `infra/kubelens.kubeconfig`(read-only ServiceAccount)다.

## 작업 방식
- 한 번에 한 Phase만 작업한다. 완료 조건을 확인하고 결과를 보고한 뒤 멈춘다.
- 도구나 프롬프트를 수정했다면 `make eval`을 실행하고 결과 표를 보고한다. (Phase 3 이후)
- 테스트 없이 "완료"라고 하지 않는다.

## 코드 스타일
- 백엔드: Python 3.12, uv, ruff, pytest, 타입 힌트 필수
- 프론트: React + Vite + TypeScript strict + Tailwind

## 디렉터리
- `backend/kubelens/tools/` 도구 레이어 (Phase 1)
- `backend/kubelens/agent/` LangGraph 에이전트 (Phase 2)
- `backend/kubelens/api/` FastAPI (Phase 4)
- `eval/` 평가 하네스 (Phase 3)
- `frontend/` React UI (Phase 5)
- `chaos/` 장애 시나리오 매니페스트, `chaos/expected.yaml`이 평가 정답지
- `infra/` kind 설정, RBAC, kubeconfig 생성 스크립트
