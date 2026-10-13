# KubeLens

자연어로 묻는 Kubernetes 클러스터 진단 도구. 조회만 하고, 모든 결론에 근거를 붙인다.

> 현재 단계: **Phase 1 — 도구 레이어** (Phase 0 환경 구성 완료)

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
