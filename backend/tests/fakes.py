"""단위 테스트용 가짜 CoreV1Api / AppsV1Api. chaos 시나리오(S1, S4, S6)를 흉내 낸다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from kubernetes import client as k
from kubernetes.client.exceptions import ApiException

NS = "kubelens-chaos"


def _ago(seconds: int) -> datetime:
    return datetime.now(UTC) - timedelta(seconds=seconds)


def _meta(name: str, ns: str | None = NS, **kw: Any) -> k.V1ObjectMeta:
    return k.V1ObjectMeta(name=name, namespace=ns, creation_timestamp=_ago(600), **kw)


def _container(name: str, image: str, **kw: Any) -> k.V1Container:
    return k.V1Container(
        name=name,
        image=image,
        resources=k.V1ResourceRequirements(
            requests={"cpu": "10m", "memory": kw.pop("memory_request", "16Mi")},
            limits={"memory": "64Mi"},
        ),
        **kw,
    )


def crashloop_pod() -> k.V1Pod:
    return k.V1Pod(
        metadata=_meta("payment-api-7d9f-abcde", labels={"app": "payment-api"}),
        spec=k.V1PodSpec(
            node_name="kubelens-worker",
            containers=[
                _container(
                    "api",
                    "busybox:1.36",
                    env=[
                        k.V1EnvVar(name="DB_PORT", value="5432"),
                        k.V1EnvVar(name="API_TOKEN", value="tok_live_123456"),
                        k.V1EnvVar(
                            name="DB_PASSWORD",
                            value_from=k.V1EnvVarSource(
                                secret_key_ref=k.V1SecretKeySelector(name="db", key="password")
                            ),
                        ),
                    ],
                )
            ],
        ),
        status=k.V1PodStatus(
            phase="Running",
            conditions=[k.V1PodCondition(type="Ready", status="False")],
            container_statuses=[
                k.V1ContainerStatus(
                    name="api",
                    image="busybox:1.36",
                    image_id="",
                    ready=False,
                    restart_count=5,
                    state=k.V1ContainerState(
                        waiting=k.V1ContainerStateWaiting(
                            reason="CrashLoopBackOff",
                            message="back-off 2m40s restarting failed container",
                        )
                    ),
                    last_state=k.V1ContainerState(
                        terminated=k.V1ContainerStateTerminated(
                            exit_code=1, reason="Error", finished_at=_ago(30)
                        )
                    ),
                )
            ],
        ),
    )


def pending_pod() -> k.V1Pod:
    return k.V1Pod(
        metadata=_meta("batch-trainer-5c6b-xyz12", labels={"app": "batch-trainer"}),
        spec=k.V1PodSpec(
            node_selector={"kubelens.io/pool": "gpu"},
            containers=[_container("trainer", "busybox:1.36", memory_request="64Gi")],
        ),
        status=k.V1PodStatus(
            phase="Pending",
            conditions=[
                k.V1PodCondition(
                    type="PodScheduled",
                    status="False",
                    reason="Unschedulable",
                    message="0/2 nodes are available: 2 Insufficient memory.",
                )
            ],
        ),
    )


def config_error_pod() -> k.V1Pod:
    return k.V1Pod(
        metadata=_meta("order-service-6f7-qwert", labels={"app": "order-service"}),
        spec=k.V1PodSpec(
            node_name="kubelens-worker",
            containers=[
                _container(
                    "order",
                    "busybox:1.36",
                    env_from=[
                        k.V1EnvFromSource(
                            config_map_ref=k.V1ConfigMapEnvSource(name="order-service-config")
                        )
                    ],
                )
            ],
        ),
        status=k.V1PodStatus(
            phase="Pending",
            container_statuses=[
                k.V1ContainerStatus(
                    name="order",
                    image="busybox:1.36",
                    image_id="",
                    ready=False,
                    restart_count=0,
                    state=k.V1ContainerState(
                        waiting=k.V1ContainerStateWaiting(
                            reason="CreateContainerConfigError",
                            message='configmap "order-service-config" not found',
                        )
                    ),
                )
            ],
        ),
    )


def healthy_pod() -> k.V1Pod:
    return k.V1Pod(
        metadata=_meta("web-ok-1", labels={"app": "web-ok"}),
        spec=k.V1PodSpec(node_name="kubelens-worker", containers=[_container("web", "nginx")]),
        status=k.V1PodStatus(
            phase="Running",
            container_statuses=[
                k.V1ContainerStatus(
                    name="web",
                    image="nginx",
                    image_id="",
                    ready=True,
                    restart_count=0,
                    state=k.V1ContainerState(
                        running=k.V1ContainerStateRunning(started_at=_ago(60))
                    ),
                )
            ],
        ),
    )


def _event(
    obj_kind: str, obj_name: str, reason: str, message: str, seconds_ago: int
) -> k.CoreV1Event:
    return k.CoreV1Event(
        metadata=_meta(f"{obj_name}.{reason.lower()}"),
        involved_object=k.V1ObjectReference(kind=obj_kind, name=obj_name, namespace=NS),
        reason=reason,
        message=message,
        type="Warning",
        count=3,
        last_timestamp=_ago(seconds_ago),
    )


def events() -> list[k.CoreV1Event]:
    return [
        _event(
            "Pod", "payment-api-7d9f-abcde", "BackOff", "Back-off restarting failed container", 20
        ),
        _event(
            "Pod",
            "batch-trainer-5c6b-xyz12",
            "FailedScheduling",
            "0/2 nodes are available: 2 Insufficient memory.",
            10,
        ),
        _event(
            "Pod",
            "order-service-6f7-qwert",
            "Failed",
            'Error: configmap "order-service-config" not found',
            5,
        ),
        _event(
            "Deployment",
            "payment-api",
            "ScalingReplicaSet",
            "Scaled up replica set payment-api-7d9f to 1",
            900,
        ),
        _event(
            "Pod", "payment-api-other", "Pulled", 'Successfully pulled image "busybox:1.36"', 1000
        ),
    ]


def deployment() -> k.V1Deployment:
    return k.V1Deployment(
        metadata=_meta(
            "payment-api",
            uid="dep-uid-1",
            annotations={"deployment.kubernetes.io/revision": "1"},
        ),
        spec=k.V1DeploymentSpec(
            replicas=1,
            selector=k.V1LabelSelector(match_labels={"app": "payment-api"}),
            strategy=k.V1DeploymentStrategy(
                type="RollingUpdate",
                rolling_update=k.V1RollingUpdateDeployment(max_surge="25%", max_unavailable="25%"),
            ),
            template=k.V1PodTemplateSpec(spec=crashloop_pod().spec),
        ),
        status=k.V1DeploymentStatus(
            replicas=1,
            updated_replicas=1,
            ready_replicas=0,
            available_replicas=0,
            unavailable_replicas=1,
            conditions=[
                k.V1DeploymentCondition(
                    type="Available",
                    status="False",
                    reason="MinimumReplicasUnavailable",
                    message="Deployment does not have minimum availability.",
                )
            ],
        ),
    )


def replica_sets() -> list[k.V1ReplicaSet]:
    owned = k.V1ReplicaSet(
        metadata=_meta(
            "payment-api-7d9f",
            annotations={"deployment.kubernetes.io/revision": "1"},
            owner_references=[
                k.V1OwnerReference(
                    api_version="apps/v1", kind="Deployment", name="payment-api", uid="dep-uid-1"
                )
            ],
        ),
        spec=k.V1ReplicaSetSpec(
            replicas=1, selector=k.V1LabelSelector(match_labels={"app": "payment-api"})
        ),
        status=k.V1ReplicaSetStatus(replicas=1, ready_replicas=0),
    )
    stranger = k.V1ReplicaSet(
        metadata=_meta("payment-api-legacy", owner_references=[]),
        spec=k.V1ReplicaSetSpec(
            replicas=1, selector=k.V1LabelSelector(match_labels={"app": "payment-api"})
        ),
        status=k.V1ReplicaSetStatus(replicas=1, ready_replicas=1),
    )
    return [owned, stranger]


def nodes() -> list[k.V1Node]:
    def node(name: str, labels: dict[str, str], taints: list[k.V1Taint] | None = None) -> k.V1Node:
        return k.V1Node(
            metadata=_meta(name, ns=None, labels=labels),
            spec=k.V1NodeSpec(taints=taints),
            status=k.V1NodeStatus(
                allocatable={"cpu": "8", "memory": "16374528Ki", "pods": "110"},
                capacity={"cpu": "8", "memory": "16374528Ki", "pods": "110"},
                conditions=[
                    k.V1NodeCondition(type="Ready", status="True"),
                    k.V1NodeCondition(type="MemoryPressure", status="False"),
                ],
                node_info=k.V1NodeSystemInfo(
                    kubelet_version="v1.33.1",
                    architecture="amd64",
                    boot_id="",
                    container_runtime_version="",
                    kernel_version="",
                    kube_proxy_version="",
                    machine_id="",
                    operating_system="linux",
                    os_image="",
                    system_uuid="",
                ),
            ),
        )

    return [
        node(
            "kubelens-control-plane",
            {"node-role.kubernetes.io/control-plane": "", "beta.kubernetes.io/os": "linux"},
            [k.V1Taint(key="node-role.kubernetes.io/control-plane", effect="NoSchedule")],
        ),
        node("kubelens-worker", {"kubelens.io/pool": "general"}),
    ]


LOG_S1 = (
    "INFO starting payment-api v1.4.2\n"
    "DEBUG loaded config api_key=kl-fake-0000000000000000\n"
    "FATAL required environment variable DB_HOST is not set\n"
)


class _List:
    def __init__(self, items: list[Any]) -> None:
        self.items = items


class FakeCore:
    def __init__(self) -> None:
        self.pods = [crashloop_pod(), pending_pod(), config_error_pod(), healthy_pod()]
        self.log_calls: list[dict[str, Any]] = []
        self.log_text = LOG_S1

    def list_namespace(self) -> _List:
        return _List(
            [
                k.V1Namespace(
                    metadata=_meta(n, ns=None), status=k.V1NamespaceStatus(phase="Active")
                )
                for n in ("default", "kube-system", NS)
            ]
        )

    def list_namespaced_pod(self, namespace: str, label_selector: str | None = None) -> _List:
        pods = [p for p in self.pods if p.metadata.namespace == namespace]
        if label_selector:
            key, _, value = label_selector.partition("=")
            pods = [p for p in pods if (p.metadata.labels or {}).get(key) == value]
        return _List(pods)

    def list_pod_for_all_namespaces(self, label_selector: str | None = None) -> _List:
        return _List(self.pods)

    def read_namespaced_pod(self, name: str, namespace: str) -> k.V1Pod:
        for p in self.pods:
            if p.metadata.name == name and p.metadata.namespace == namespace:
                return p
        raise ApiException(status=404, reason="Not Found")

    def read_namespaced_pod_log(self, name: str, namespace: str, **kwargs: Any) -> str:
        self.log_calls.append({"name": name, "namespace": namespace, **kwargs})
        return self.log_text

    def list_namespaced_event(self, namespace: str) -> _List:
        return _List([e for e in events() if e.metadata.namespace == namespace])

    def list_event_for_all_namespaces(self) -> _List:
        return _List(events())

    def list_node(self) -> _List:
        return _List(nodes())


class FakeApps:
    def list_namespaced_deployment(self, namespace: str) -> _List:
        return _List([deployment()] if namespace == NS else [])

    def list_deployment_for_all_namespaces(self) -> _List:
        return _List([deployment()])

    def read_namespaced_deployment(self, name: str, namespace: str) -> k.V1Deployment:
        if name == "payment-api" and namespace == NS:
            return deployment()
        raise ApiException(status=404, reason="Not Found")

    def list_namespaced_replica_set(
        self, namespace: str, label_selector: str | None = None
    ) -> _List:
        return _List(replica_sets())


class FakeVersion:
    def get_code(self) -> k.VersionInfo:
        return k.VersionInfo(
            git_version="v1.33.1",
            build_date="",
            compiler="",
            git_commit="",
            git_tree_state="",
            go_version="",
            major="1",
            minor="33",
            platform="linux/amd64",
        )


def fake_kube(kubeconfig_path: str | None = None, context: str | None = None) -> Any:
    """KUBELENS_KUBE_FACTORY=tests.fakes:fake_kube 로 클러스터 없이 API/UI를 띄울 때 쓴다."""
    from kubelens.tools import ReadOnlyKube

    return ReadOnlyKube(FakeCore(), FakeApps(), FakeVersion())
