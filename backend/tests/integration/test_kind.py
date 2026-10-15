"""kind 클러스터 대상 통합 테스트 (기획서 14장 Phase 1 완료 조건).

전제: 저장소 루트에서 `make phase0` 완료 (infra/kubelens.kubeconfig, chaos 시나리오 배포).
클러스터에 연결할 수 없으면 전체를 skip한다. 실행: `make test-kind`
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from kubelens.tools import (
    describe_deployment,
    describe_pod,
    get_events,
    get_nodes,
    get_pod_logs,
    list_deployments,
    list_namespaces,
    list_pods,
    run_tool,
)
from kubelens.tools.kube import ReadOnlyKube, default_kubeconfig_path

pytestmark = pytest.mark.kind

NS = "kubelens-chaos"
KUBECONFIG: Path = default_kubeconfig_path()


def unavailable(reason: str) -> None:
    """make test-kind(KUBELENS_REQUIRE_KIND=1)에서는 skip 대신 실패시킨다."""
    if os.environ.get("KUBELENS_REQUIRE_KIND") == "1":
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def kube() -> ReadOnlyKube:
    if not KUBECONFIG.exists():
        unavailable(f"{KUBECONFIG} 없음 — 먼저 `make phase0` 실행")
    kube = ReadOnlyKube.from_kubeconfig(KUBECONFIG)
    try:
        kube.list_namespace()
    except Exception as exc:  # 연결 불가
        unavailable(f"kind 클러스터에 연결할 수 없음: {exc}")
    return kube


def pod_name(kube: ReadOnlyKube, scenario: str) -> str:
    pods = kube.list_pod(NS, f"kubelens.io/scenario={scenario}").items
    assert pods, f"{scenario} 파드가 없음 — `make chaos-up` 확인"
    return pods[0].metadata.name


def test_list_namespaces(kube: ReadOnlyKube) -> None:
    out = list_namespaces(kube)
    assert NS in out
    assert "kube-system" in out


def test_list_pods_only_unhealthy_has_all_six(kube: ReadOnlyKube) -> None:
    out = list_pods(kube, NS, only_unhealthy=True)
    for dep in (
        "payment-api",
        "web-frontend",
        "report-worker",
        "batch-trainer",
        "gpu-inference",
        "order-service",
    ):
        assert dep in out, out


def test_s1_crashloop_describe_and_previous_logs(kube: ReadOnlyKube) -> None:
    name = pod_name(kube, "s1")
    desc = describe_pod(kube, NS, name)
    assert "CrashLoopBackOff" in desc or "Error" in desc
    assert "exit code 1" in desc
    # 컨테이너가 막 재시작된 순간에는 kubelet이 직전 컨테이너 로그를 돌려주지 못할 때가 있다
    # ("unable to retrieve container logs"). 직전/현재 컨테이너 로그를 잠시 번갈아 확인한다.
    logs = ""
    for _ in range(10):
        for previous in (True, False):
            logs = get_pod_logs(kube, NS, name, previous=previous)
            if "DB_HOST" in logs:
                break
        if "DB_HOST" in logs:
            break
        time.sleep(3)
    assert "DB_HOST" in logs, logs
    assert "kl-fake-0000000000000000" not in logs  # 마스킹
    assert "api_key=***" in logs


def test_s2_image_pull(kube: ReadOnlyKube) -> None:
    desc = describe_pod(kube, NS, pod_name(kube, "s2"))
    assert "nginx:9.99.99-doesnotexist" in desc
    assert "ImagePullBackOff" in desc or "ErrImagePull" in desc


def test_s3_oomkilled(kube: ReadOnlyKube) -> None:
    desc = describe_pod(kube, NS, pod_name(kube, "s3"))
    assert "OOMKilled" in desc
    assert "memory=32Mi" in desc


def test_s4_insufficient_memory(kube: ReadOnlyKube) -> None:
    desc = describe_pod(kube, NS, pod_name(kube, "s4"))
    assert "Insufficient memory" in desc
    assert "memory=64Gi" in desc
    events = get_events(kube, NS, involved_object="batch-trainer")
    assert "FailedScheduling" in events


def test_s5_node_selector(kube: ReadOnlyKube) -> None:
    desc = describe_pod(kube, NS, pod_name(kube, "s5"))
    assert "Node-Selector: kubelens.io/pool=gpu" in desc
    assert "affinity/selector" in desc
    assert "kubelens.io/pool=general" in get_nodes(kube)


def test_s6_missing_configmap(kube: ReadOnlyKube) -> None:
    desc = describe_pod(kube, NS, pod_name(kube, "s6"))
    assert "CreateContainerConfigError" in desc
    assert "order-service-config" in desc


def test_deployments(kube: ReadOnlyKube) -> None:
    out = list_deployments(kube, NS)
    assert "payment-api" in out and "0/1" in out
    desc = describe_deployment(kube, NS, "payment-api")
    assert "Image: busybox:1.36" in desc
    assert "ReplicaSets:" in desc and "payment-api-" in desc


def test_outputs_within_limit(kube: ReadOnlyKube) -> None:
    for name, args in [
        ("get_events", {"namespace": "*"}),
        ("list_pods", {"namespace": "*"}),
        ("get_nodes", {}),
    ]:
        result = run_tool(kube, name, args)
        assert result.status == "ok", result.output
        assert len(result.output) <= 4000


def test_run_tool_reports_missing_pod(kube: ReadOnlyKube) -> None:
    result = run_tool(kube, "describe_pod", {"namespace": NS, "name": "does-not-exist"})
    assert result.status == "error"
    assert "404" in result.output


def test_service_account_cannot_read_secrets() -> None:
    """RBAC 두 번째 방어선: 도구 레이어를 우회한 원본 client로도 secret 조회는 403이어야 한다.

    쓰기 거부는 상태 변경 시도 자체를 피하기 위해 infra/rbac-check.sh(auth can-i)로 검사한다.
    """
    if not KUBECONFIG.exists():
        unavailable(f"{KUBECONFIG} 없음 — 먼저 `make phase0` 실행")
    api = client.CoreV1Api(config.new_client_from_config(config_file=str(KUBECONFIG)))
    try:
        api.list_namespace()
    except Exception as exc:
        unavailable(f"kind 클러스터에 연결할 수 없음: {exc}")
    with pytest.raises(ApiException) as exc_info:
        api.list_namespaced_secret("kube-system")
    assert exc_info.value.status == 403
