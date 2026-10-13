"""가짜 API 객체를 대상으로 한 도구 단위 테스트 (클러스터 불필요)."""

from kubelens.tools import (
    TOOLS,
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
from tests.fakes import NS, FakeCore

EXPECTED_TOOLS = {
    "list_namespaces",
    "list_pods",
    "describe_pod",
    "get_pod_logs",
    "get_events",
    "list_deployments",
    "describe_deployment",
    "get_nodes",
}


def test_allowlist_is_exactly_the_spec() -> None:
    assert set(TOOLS) == EXPECTED_TOOLS


def test_list_namespaces(kube) -> None:
    out = list_namespaces(kube)
    assert out.splitlines()[0].split() == ["NAME", "STATUS", "AGE"]
    assert NS in out and "Active" in out


def test_list_pods_status_and_restarts(kube) -> None:
    out = list_pods(kube, NS)
    row = next(line for line in out.splitlines() if line.startswith("payment-api"))
    assert "CrashLoopBackOff" in row
    assert row.split()[1:4] == ["0/1", "CrashLoopBackOff", "5"]
    assert "CreateContainerConfigError" in out
    assert "Pending" in out


def test_list_pods_only_unhealthy_excludes_healthy(kube) -> None:
    out = list_pods(kube, NS, only_unhealthy=True)
    assert "web-ok-1" not in out
    for name in ("payment-api", "batch-trainer", "order-service"):
        assert name in out


def test_list_pods_label_selector_and_empty(kube) -> None:
    assert "payment-api" in list_pods(kube, NS, label_selector="app=payment-api")
    assert "batch-trainer" not in list_pods(kube, NS, label_selector="app=payment-api")
    assert list_pods(kube, "empty-ns") == "No pods found in namespace empty-ns."


def test_list_pods_all_namespaces_adds_column(kube) -> None:
    out = list_pods(kube, "*")
    assert out.splitlines()[0].startswith("NAMESPACE")


def test_describe_pod_crashloop(kube) -> None:
    out = describe_pod(kube, NS, "payment-api-7d9f-abcde")
    assert "Status: CrashLoopBackOff" in out
    assert "Last State: Terminated (Error) exit code 1" in out
    assert "Restarts: 5" in out
    assert "requests=cpu=10m,memory=16Mi" in out
    assert "DB_PORT=5432" in out
    assert "BackOff" in out  # 해당 파드 이벤트
    assert "ScalingReplicaSet" not in out  # 다른 객체 이벤트는 제외


def test_describe_pod_never_reads_secret_values_and_masks_env(kube) -> None:
    out = describe_pod(kube, NS, "payment-api-7d9f-abcde")
    assert "DB_PASSWORD from secret db key password (value not read)" in out
    assert "tok_live_123456" not in out
    assert "API_TOKEN=***" in out


def test_describe_pod_pending(kube) -> None:
    out = describe_pod(kube, NS, "batch-trainer-5c6b-xyz12")
    assert "PodScheduled=False reason=Unschedulable" in out
    assert "Insufficient memory" in out
    assert "memory=64Gi" in out
    assert "Node-Selector: kubelens.io/pool=gpu" in out
    assert "State: <not created>" in out


def test_describe_pod_config_error(kube) -> None:
    out = describe_pod(kube, NS, "order-service-6f7-qwert")
    assert "CreateContainerConfigError" in out
    assert "EnvFrom: configmap order-service-config" in out


def test_get_pod_logs_masks_and_caps_tail(kube, core: FakeCore) -> None:
    out = get_pod_logs(kube, NS, "payment-api-7d9f-abcde", previous=True, tail=5000)
    assert "DB_HOST is not set" in out
    assert "kl-fake-0000000000000000" not in out
    assert "api_key=***" in out
    call = core.log_calls[-1]
    assert call["tail_lines"] == 200
    assert call["previous"] is True
    assert "container" not in call


def test_get_pod_logs_passes_container_and_handles_empty(kube, core: FakeCore) -> None:
    core.log_text = ""
    out = get_pod_logs(kube, NS, "payment-api-7d9f-abcde", container="api", tail=10)
    assert "(no log output)" in out
    assert core.log_calls[-1]["container"] == "api"
    assert core.log_calls[-1]["tail_lines"] == 10


def test_get_pod_logs_truncated_to_4000(kube, core: FakeCore) -> None:
    core.log_text = "\n".join(f"line {i} " + "y" * 80 for i in range(200))
    result = run_tool(kube, "get_pod_logs", {"namespace": NS, "name": "payment-api-7d9f-abcde"})
    assert result.truncated
    assert len(result.output) <= 4000
    assert "line 199" in result.output  # 최신 로그(끝부분)는 살아 있어야 한다


def test_get_events_latest_first_and_filter(kube) -> None:
    out = get_events(kube, NS)
    lines = out.splitlines()
    assert lines[0].split()[:3] == ["LAST", "SEEN", "TYPE"]
    assert "order-service" in lines[1]  # 가장 최근 이벤트
    filtered = get_events(kube, NS, involved_object="payment-api")
    assert "ScalingReplicaSet" in filtered  # Deployment 자체
    assert "BackOff" in filtered  # 하위 파드
    assert "batch-trainer" not in filtered
    assert len(get_events(kube, NS, limit=2).splitlines()) == 3


def test_get_events_empty(kube) -> None:
    assert (
        get_events(kube, NS, involved_object="nope")
        == "No events found for nope in namespace kubelens-chaos."
    )


def test_list_deployments(kube) -> None:
    out = list_deployments(kube, NS)
    row = out.splitlines()[1].split()
    assert row[:4] == ["payment-api", "0/1", "1", "0"]
    assert "busybox:1.36" in out


def test_describe_deployment(kube) -> None:
    out = describe_deployment(kube, NS, "payment-api")
    assert "Replicas: 1 desired | 1 updated | 0 ready | 0 available | 1 unavailable" in out
    assert "Available=False reason=MinimumReplicasUnavailable" in out
    assert "Image: busybox:1.36" in out
    assert "payment-api-7d9f" in out
    assert "payment-api-legacy" not in out  # 소유하지 않은 ReplicaSet은 제외
    assert "BackOff" in out


def test_get_nodes(kube) -> None:
    out = get_nodes(kube)
    assert "kubelens-worker" in out
    assert "kubelens.io/pool=general" in out
    assert "node-role.kubernetes.io/control-plane:NoSchedule" in out
    assert "16374528Ki (~15.6Gi)" in out
    assert "control-plane" in out.splitlines()[1]
    assert "beta.kubernetes.io" not in out


def test_run_tool_ok_result(kube) -> None:
    result = run_tool(kube, "list_pods", {"namespace": NS, "only_unhealthy": True})
    assert result.status == "ok"
    assert result.tool_name == "list_pods"
    assert result.args == {"namespace": NS, "only_unhealthy": True}
    assert not result.truncated
    assert result.duration_ms >= 0


def test_run_tool_rejects_unknown_tool(kube) -> None:
    result = run_tool(kube, "delete_pod", {"namespace": NS, "name": "x"})
    assert result.status == "error"
    assert "unknown tool" in result.output


def test_run_tool_rejects_bad_args(kube) -> None:
    result = run_tool(kube, "describe_pod", {"namespace": NS})
    assert result.status == "error"
    assert "invalid arguments" in result.output


def test_run_tool_api_error_becomes_error_result(kube) -> None:
    result = run_tool(kube, "describe_pod", {"namespace": NS, "name": "ghost"})
    assert result.status == "error"
    assert result.output.startswith("ERROR 404")
