CLUSTER   := kubelens
CTX       := kind-$(CLUSTER)
KUBECTL   := kubectl --context $(CTX)
CHAOS_NS  := kubelens-chaos

BACKEND   := backend
UV        := uv --directory $(BACKEND)

.PHONY: phase0 cluster-up cluster-down rbac rbac-check chaos-up chaos-down chaos-verify chaos-status \
        setup lint test test-unit test-kind ask eval api sse-smoke

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

setup:
	$(UV) sync

lint:
	$(UV) run ruff check . ../eval
	$(UV) run ruff format --check . ../eval

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

# 진단 질문 (ANTHROPIC_API_KEY 필요). 예: make ask Q="payment-api가 계속 재시작돼"
ask:
	$(UV) run python -m kubelens ask "$(Q)"
