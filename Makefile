CLUSTER   := kubelens
CTX       := kind-$(CLUSTER)
KUBECTL   := kubectl --context $(CTX)
CHAOS_NS  := kubelens-chaos

BACKEND   := backend
FRONTEND  := frontend
UV        := uv --directory $(BACKEND)
NPM       := npm --prefix $(FRONTEND)

.PHONY: phase0 cluster-up cluster-down rbac rbac-check chaos-up chaos-down chaos-verify chaos-status \
        setup lint test test-unit test-kind ask eval api sse-smoke ui-dev e2e demo-gif

phase0: cluster-up rbac rbac-check chaos-up chaos-verify
	@echo ""
	@echo "Phase 0 완료 ✅"

cluster-up:
	@if kind get clusters | grep -qx "$(CLUSTER)"; then \
		echo "kind 클러스터 '$(CLUSTER)' 이미 존재"; \
	else \
		kind create cluster --name $(CLUSTER) --config infra/kind-config.yaml; \
	fi
	$(KUBECTL) wait --for=condition=Ready nodes --all --timeout=120s

cluster-down:
	kind delete cluster --name $(CLUSTER)

rbac:
	$(KUBECTL) apply -f infra/rbac.yaml
	CTX=$(CTX) bash infra/gen-kubeconfig.sh

rbac-check:
	CTX=$(CTX) bash infra/rbac-check.sh

chaos-up:
	$(KUBECTL) apply -f chaos/namespace.yaml
	$(KUBECTL) apply -f chaos/scenarios/

chaos-down:
	$(KUBECTL) delete namespace $(CHAOS_NS) --ignore-not-found

chaos-verify:
	CTX=$(CTX) NS=$(CHAOS_NS) bash chaos/verify.sh

chaos-status:
	$(KUBECTL) get pods -n $(CHAOS_NS) -o wide

# --- 백엔드 (Phase 1~) ------------------------------------------------------

# 백엔드 의존성 + 프런트 빌드 (빌드된 UI는 make api가 함께 서빙한다)
setup:
	$(UV) sync
	$(NPM) ci
	$(NPM) run build

lint:
	$(UV) run ruff check . ../eval
	$(UV) run ruff format --check . ../eval
	$(NPM) run lint
	$(NPM) run typecheck

test-unit:
	$(UV) run pytest -m "not kind" tests ../eval

# kind 클러스터 대상 (make phase0 선행). 클러스터가 없으면 skip이 아니라 실패한다.
test-kind:
	KUBELENS_REQUIRE_KIND=1 $(UV) run pytest -m kind

test: lint test-unit test-kind

# 평가 하네스 (kind + ANTHROPIC_API_KEY 필요). 예: make eval SCENARIOS=S1,S3
eval:
	$(UV) run python ../eval/harness.py $(if $(SCENARIOS),--scenarios $(SCENARIOS))

# API 서버 (frontend/dist가 있으면 UI도 함께 서빙). ANTHROPIC_API_KEY 필요
api:
	$(UV) run uvicorn kubelens.api.app:app --host 127.0.0.1 --port 8000

# 실행 중인 API에 질문을 보내 SSE 이벤트 순서를 검사한다 (Phase 4 완료 조건)
sse-smoke:
	bash $(BACKEND)/scripts/sse-smoke.sh

# 프런트 개발 서버 (http://localhost:5173, /api는 make api로 프록시)
ui-dev:
	$(NPM) run dev

# 실행 중인 서버(기본 :8000)를 대상으로 Playwright e2e
e2e:
	$(NPM) run e2e

# 실행 중인 make api(실제 API 키)로 S1 진단 과정을 녹화해 docs/demo.gif 생성 (ffmpeg 필요)
demo-gif:
	bash infra/demo-gif.sh

# 진단 질문 (ANTHROPIC_API_KEY 필요). 예: make ask Q="payment-api가 계속 재시작돼"
ask:
	$(UV) run python -m kubelens ask "$(Q)"
