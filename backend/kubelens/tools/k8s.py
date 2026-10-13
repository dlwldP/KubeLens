"""KubeLens 도구 8종 (기획서 8장). 이 외의 도구는 존재하지 않는다.

모든 도구는 ReadOnlyKube를 통해 조회만 하며, 사람이 읽는 kubectl 스타일 텍스트를 반환한다.
namespace 인자에 "*"를 주면 전체 네임스페이스를 조회한다 (전체 헬스 체크용).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from kubelens.tools.base import tool
from kubelens.tools.formatting import age, humanize_memory, kv, one_line, table
from kubelens.tools.kube import ALL_NAMESPACES, ReadOnlyKube

MAX_LOG_TAIL = 200
MAX_EVENTS = 50
DESCRIBE_EVENTS = 10

_HEALTHY_STATUSES = {"Running", "Succeeded", "Completed"}


# --- 공통 헬퍼 -------------------------------------------------------------


def pod_status(pod: Any) -> str:
    """kubectl get pods의 STATUS 컬럼과 비슷한 값 (CrashLoopBackOff, Pending 등)."""
    if pod.metadata.deletion_timestamp:
        return "Terminating"
    status = pod.status
    for cs in status.init_container_statuses or []:
        state = cs.state
        if state and state.waiting and state.waiting.reason not in (None, "PodInitializing"):
            return f"Init:{state.waiting.reason}"
        if state and state.terminated and state.terminated.exit_code != 0:
            return f"Init:{state.terminated.reason or 'Error'}"
    for cs in status.container_statuses or []:
        state = cs.state
        if state and state.waiting and state.waiting.reason:
            return state.waiting.reason
        if state and state.terminated:
            return state.terminated.reason or f"ExitCode:{state.terminated.exit_code}"
    return status.reason or status.phase or "Unknown"


def _restarts(pod: Any) -> int:
    return sum(cs.restart_count or 0 for cs in pod.status.container_statuses or [])


def _ready(pod: Any) -> tuple[int, int]:
    statuses = pod.status.container_statuses or []
    total = len(pod.spec.containers or [])
    return sum(1 for cs in statuses if cs.ready), total


def is_unhealthy(pod: Any) -> bool:
    status = pod_status(pod)
    if status not in _HEALTHY_STATUSES:
        return True
    ready, total = _ready(pod)
    if status == "Running" and ready < total:
        return True
    return _restarts(pod) > 0


def _event_time(ev: Any) -> datetime | None:
    return (
        ev.last_timestamp
        or ev.event_time
        or ev.first_timestamp
        or (ev.metadata.creation_timestamp if ev.metadata else None)
    )


def _matches_object(object_name: str | None, query: str) -> bool:
    """정확히 일치하거나 query로 시작하는 하위 리소스(Deployment → ReplicaSet → Pod)."""
    if not object_name:
        return False
    return object_name == query or object_name.startswith(f"{query}-")


def _sorted_events(kube: ReadOnlyKube, namespace: str, involved_object: str | None) -> list[Any]:
    events = list(kube.list_event(namespace).items or [])
    if involved_object:
        events = [e for e in events if _matches_object(e.involved_object.name, involved_object)]
    epoch = datetime.min

    def key(e: Any) -> datetime:
        ts = _event_time(e)
        return ts.replace(tzinfo=None) if ts else epoch

    return sorted(events, key=key, reverse=True)


def _events_table(events: list[Any], with_namespace: bool = False) -> str:
    headers = ["LAST SEEN", "TYPE", "REASON", "OBJECT", "COUNT", "MESSAGE"]
    rows = []
    for e in events:
        obj = f"{(e.involved_object.kind or '').lower()}/{e.involved_object.name}"
        row = [age(_event_time(e)), e.type or "", e.reason or "", obj, e.count or 1]
        row.append(one_line(e.message))
        if with_namespace:
            row.insert(0, e.metadata.namespace if e.metadata else "")
        rows.append(row)
    if with_namespace:
        headers = ["NAMESPACE", *headers]
    return table(headers, rows)


def _resources(resources: Any) -> str:
    if resources is None:
        return "requests=<none> limits=<none>"
    req = {k: v for k, v in (resources.requests or {}).items()}
    lim = {k: v for k, v in (resources.limits or {}).items()}
    return f"requests={kv(req, ',')} limits={kv(lim, ',')}"


def _env_lines(container: Any) -> list[str]:
    """env 값은 마스킹 대상이며, secret 참조는 이름만 표시하고 값은 절대 조회하지 않는다."""
    parts: list[str] = []
    for env in container.env or []:
        if env.value_from is None:
            parts.append(f"{env.name}={env.value if env.value is not None else ''}")
            continue
        vf = env.value_from
        if vf.config_map_key_ref:
            ref = vf.config_map_key_ref
            parts.append(f"{env.name} from configmap {ref.name} key {ref.key}")
        elif vf.secret_key_ref:
            ref = vf.secret_key_ref
            parts.append(f"{env.name} from secret {ref.name} key {ref.key} (value not read)")
        elif vf.field_ref:
            parts.append(f"{env.name} from field {vf.field_ref.field_path}")
        elif vf.resource_field_ref:
            parts.append(f"{env.name} from resource {vf.resource_field_ref.resource}")
        else:
            parts.append(f"{env.name} from <valueFrom>")
    lines = [f"Env: {'; '.join(parts) if parts else '<none>'}"]
    sources = []
    for src in container.env_from or []:
        prefix = f" (prefix {src.prefix})" if src.prefix else ""
        if src.config_map_ref:
            sources.append(f"configmap {src.config_map_ref.name}{prefix}")
        if src.secret_ref:
            sources.append(f"secret {src.secret_ref.name}{prefix}")
    if sources:
        lines.append(f"EnvFrom: {'; '.join(sources)}")
    return lines


def _state_text(state: Any) -> str:
    if state is None:
        return "<none>"
    if state.waiting:
        msg = f": {one_line(state.waiting.message)}" if state.waiting.message else ""
        return f"Waiting ({state.waiting.reason}){msg}"
    if state.running:
        return f"Running (started {age(state.running.started_at)} ago)"
    if state.terminated:
        t = state.terminated
        msg = f": {one_line(t.message)}" if t.message else ""
        return (
            f"Terminated ({t.reason}) exit code {t.exit_code}, "
            f"finished {age(t.finished_at)} ago{msg}"
        )
    return "<none>"


def _affinity_text(affinity: Any) -> str | None:
    node_aff = affinity.node_affinity if affinity else None
    required = node_aff.required_during_scheduling_ignored_during_execution if node_aff else None
    if not required:
        return None
    terms = []
    for term in required.node_selector_terms or []:
        exprs = [
            f"{e.key} {e.operator} {','.join(e.values or [])}".strip()
            for e in term.match_expressions or []
        ]
        terms.append(" AND ".join(exprs))
    return " OR ".join(f"({t})" for t in terms)


def _container_block(spec: Any, status: Any | None) -> list[str]:
    lines = [f"  - {spec.name}", f"    Image: {spec.image}"]
    if status is not None:
        lines.append(f"    State: {_state_text(status.state)}")
        if status.last_state and (status.last_state.terminated or status.last_state.waiting):
            lines.append(f"    Last State: {_state_text(status.last_state)}")
        lines.append(f"    Ready: {bool(status.ready)}   Restarts: {status.restart_count or 0}")
    else:
        lines.append("    State: <not created>")
    lines.append(f"    Resources: {_resources(spec.resources)}")
    lines.extend(f"    {line}" for line in _env_lines(spec))
    return lines


# --- 도구 8종 -------------------------------------------------------------


@tool
def list_namespaces(kube: ReadOnlyKube) -> str:
    """클러스터의 네임스페이스 목록과 상태."""
    items = kube.list_namespace().items or []
    rows = [
        [n.metadata.name, n.status.phase or "", age(n.metadata.creation_timestamp)] for n in items
    ]
    return table(["NAME", "STATUS", "AGE"], rows)


@tool
def list_pods(
    kube: ReadOnlyKube,
    namespace: str,
    label_selector: str | None = None,
    only_unhealthy: bool = False,
) -> str:
    """파드 목록(상태, 준비 수, 재시작 횟수, 노드).

    namespace="*"면 전체 네임스페이스. only_unhealthy=True면 비정상 파드만.
    """
    pods = list(kube.list_pod(namespace, label_selector).items or [])
    if only_unhealthy:
        pods = [p for p in pods if is_unhealthy(p)]
    if not pods:
        scope = "all namespaces" if namespace == ALL_NAMESPACES else f"namespace {namespace}"
        qualifier = "unhealthy " if only_unhealthy else ""
        return f"No {qualifier}pods found in {scope}."
    all_ns = namespace == ALL_NAMESPACES
    rows = []
    for p in sorted(pods, key=lambda p: (p.metadata.namespace or "", p.metadata.name)):
        ready, total = _ready(p)
        row = [
            p.metadata.name,
            f"{ready}/{total}",
            pod_status(p),
            _restarts(p),
            age(p.metadata.creation_timestamp),
            p.spec.node_name or "<none>",
        ]
        if all_ns:
            row.insert(0, p.metadata.namespace)
        rows.append(row)
    headers = ["NAME", "READY", "STATUS", "RESTARTS", "AGE", "NODE"]
    if all_ns:
        headers = ["NAMESPACE", *headers]
    return table(headers, rows)


@tool
def describe_pod(kube: ReadOnlyKube, namespace: str, name: str) -> str:
    """파드 상세: 컨테이너 상태, 마지막 종료 사유·exit code, 리소스, 스케줄 조건, 최근 이벤트."""
    pod = kube.read_pod(namespace, name)
    spec, status = pod.spec, pod.status
    lines = [
        f"Name: {pod.metadata.name}",
        f"Namespace: {pod.metadata.namespace}",
        f"Node: {spec.node_name or '<none>'}",
        f"Status: {pod_status(pod)} (phase: {status.phase})",
        f"Age: {age(pod.metadata.creation_timestamp)}",
        f"Labels: {kv(pod.metadata.labels)}",
        f"Node-Selector: {kv(spec.node_selector)}",
    ]
    affinity = _affinity_text(spec.affinity)
    if affinity:
        lines.append(f"Node-Affinity (required): {affinity}")
    if spec.tolerations:
        tols = [
            f"{t.key or '*'}{'=' + t.value if t.value else ''}:{t.effect or '*'}"
            for t in spec.tolerations
        ]
        lines.append(f"Tolerations: {', '.join(tols)}")
    lines.append("Conditions:")
    for c in status.conditions or []:
        detail = f" reason={c.reason}" if c.reason else ""
        detail += f": {one_line(c.message)}" if c.message else ""
        lines.append(f"  {c.type}={c.status}{detail}")

    statuses = {cs.name: cs for cs in status.container_statuses or []}
    init_statuses = {cs.name: cs for cs in status.init_container_statuses or []}
    if spec.init_containers:
        lines.append("Init Containers:")
        for c in spec.init_containers:
            lines.extend(_container_block(c, init_statuses.get(c.name)))
    lines.append("Containers:")
    for c in spec.containers or []:
        lines.extend(_container_block(c, statuses.get(c.name)))

    events = [e for e in _sorted_events(kube, namespace, None) if e.involved_object.name == name][
        :DESCRIBE_EVENTS
    ]
    lines.append(f"Events (latest {DESCRIBE_EVENTS}):")
    lines.append(_indent(_events_table(events)) if events else "  <none>")
    return "\n".join(lines)


@tool
def get_pod_logs(
    kube: ReadOnlyKube,
    namespace: str,
    name: str,
    container: str | None = None,
    previous: bool = False,
    tail: int = MAX_LOG_TAIL,
) -> str:
    """컨테이너 로그 마지막 tail줄(최대 200). previous=True면 직전에 종료된 컨테이너의 로그."""
    tail = max(1, min(int(tail), MAX_LOG_TAIL))
    text = kube.read_pod_log(
        namespace, name, container=container, previous=previous, tail_lines=tail
    )
    header = f"# logs {namespace}/{name}"
    if container:
        header += f" container={container}"
    header += f" previous={previous} tail={tail}"
    body = text.rstrip("\n") if text and text.strip() else "(no log output)"
    return f"{header}\n{body}"


@tool
def get_events(
    kube: ReadOnlyKube,
    namespace: str,
    involved_object: str | None = None,
    limit: int = MAX_EVENTS,
) -> str:
    """이벤트 최신순(최대 50).

    involved_object를 주면 그 이름이거나 그 이름으로 시작하는 하위 리소스(RS, Pod)만.
    """
    limit = max(1, min(int(limit), MAX_EVENTS))
    events = _sorted_events(kube, namespace, involved_object)[:limit]
    if not events:
        target = f" for {involved_object}" if involved_object else ""
        return f"No events found{target} in namespace {namespace}."
    return _events_table(events, with_namespace=namespace == ALL_NAMESPACES)


@tool
def list_deployments(kube: ReadOnlyKube, namespace: str) -> str:
    """디플로이먼트 목록(desired/ready/up-to-date/available, 이미지)."""
    deps = list(kube.list_deployment(namespace).items or [])
    if not deps:
        return f"No deployments found in namespace {namespace}."
    all_ns = namespace == ALL_NAMESPACES
    rows = []
    for d in sorted(deps, key=lambda d: (d.metadata.namespace or "", d.metadata.name)):
        st = d.status
        images = ",".join(c.image for c in d.spec.template.spec.containers or [])
        row = [
            d.metadata.name,
            f"{st.ready_replicas or 0}/{d.spec.replicas if d.spec.replicas is not None else 1}",
            st.updated_replicas or 0,
            st.available_replicas or 0,
            age(d.metadata.creation_timestamp),
            images,
        ]
        if all_ns:
            row.insert(0, d.metadata.namespace)
        rows.append(row)
    headers = ["NAME", "READY", "UP-TO-DATE", "AVAILABLE", "AGE", "IMAGES"]
    if all_ns:
        headers = ["NAMESPACE", *headers]
    return table(headers, rows)


@tool
def describe_deployment(kube: ReadOnlyKube, namespace: str, name: str) -> str:
    """디플로이먼트 상세: 롤아웃 상태·조건, 이미지, 파드 템플릿, ReplicaSet, 관련 이벤트."""
    d = kube.read_deployment(namespace, name)
    spec, st = d.spec, d.status
    desired = spec.replicas if spec.replicas is not None else 1
    lines = [
        f"Name: {d.metadata.name}",
        f"Namespace: {d.metadata.namespace}",
        f"Age: {age(d.metadata.creation_timestamp)}",
        f"Revision: {(d.metadata.annotations or {}).get('deployment.kubernetes.io/revision', '?')}",
        f"Selector: {kv(spec.selector.match_labels if spec.selector else None)}",
        (
            f"Replicas: {desired} desired | {st.updated_replicas or 0} updated | "
            f"{st.ready_replicas or 0} ready | {st.available_replicas or 0} available | "
            f"{st.unavailable_replicas or 0} unavailable"
        ),
    ]
    strategy = spec.strategy
    if strategy:
        extra = ""
        if strategy.rolling_update:
            ru = strategy.rolling_update
            extra = f" (maxSurge={ru.max_surge}, maxUnavailable={ru.max_unavailable})"
        lines.append(f"Strategy: {strategy.type}{extra}")
    lines.append("Conditions:")
    for c in st.conditions or []:
        detail = f" reason={c.reason}" if c.reason else ""
        detail += f": {one_line(c.message)}" if c.message else ""
        lines.append(f"  {c.type}={c.status}{detail}")

    pod_spec = spec.template.spec
    lines.append("Pod Template:")
    lines.append(f"  Node-Selector: {kv(pod_spec.node_selector)}")
    affinity = _affinity_text(pod_spec.affinity)
    if affinity:
        lines.append(f"  Node-Affinity (required): {affinity}")
    lines.append("  Containers:")
    for c in pod_spec.containers or []:
        lines.append(f"  - {c.name}")
        lines.append(f"    Image: {c.image}")
        lines.append(f"    Resources: {_resources(c.resources)}")
        lines.extend(f"    {line}" for line in _env_lines(c))

    selector = kv(spec.selector.match_labels, ",") if spec.selector else None
    rs_items = kube.list_replica_set(namespace, selector if selector != "<none>" else None).items
    owned = [
        rs
        for rs in rs_items or []
        if any(ref.uid == d.metadata.uid for ref in rs.metadata.owner_references or [])
    ]
    lines.append("ReplicaSets:")
    if owned:
        rows = [
            [
                rs.metadata.name,
                (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "?"),
                f"{rs.status.ready_replicas or 0}/{rs.spec.replicas or 0}",
                age(rs.metadata.creation_timestamp),
            ]
            for rs in sorted(owned, key=lambda r: r.metadata.name)
        ]
        lines.append(_indent(table(["NAME", "REVISION", "READY", "AGE"], rows)))
    else:
        lines.append("  <none>")

    events = _sorted_events(kube, namespace, name)[:DESCRIBE_EVENTS]
    lines.append(f"Events for {name} and its ReplicaSets/Pods (latest {DESCRIBE_EVENTS}):")
    lines.append(_indent(_events_table(events)) if events else "  <none>")
    return "\n".join(lines)


@tool
def get_nodes(kube: ReadOnlyKube) -> str:
    """노드 목록: Ready 상태, 역할, 라벨, taint, allocatable/capacity, 압박(pressure) 조건."""
    nodes = sorted(kube.list_node().items or [], key=lambda n: n.metadata.name)
    if not nodes:
        return "No nodes found."
    summary_rows = []
    details: list[str] = []
    for n in nodes:
        labels = n.metadata.labels or {}
        roles = sorted(
            k.split("/", 1)[1] for k in labels if k.startswith("node-role.kubernetes.io/")
        )
        conditions = n.status.conditions or []
        ready = next((c for c in conditions if c.type == "Ready"), None)
        ready_text = "Ready" if ready and ready.status == "True" else "NotReady"
        if n.spec.unschedulable:
            ready_text += ",SchedulingDisabled"
        summary_rows.append(
            [
                n.metadata.name,
                ready_text,
                ",".join(roles) or "<none>",
                age(n.metadata.creation_timestamp),
                n.status.node_info.kubelet_version if n.status.node_info else "",
            ]
        )
        alloc = n.status.allocatable or {}
        cap = n.status.capacity or {}
        shown_labels = {k: v for k, v in labels.items() if not k.startswith("beta.kubernetes.io/")}
        taints = [
            f"{t.key}{'=' + t.value if t.value else ''}:{t.effect}" for t in n.spec.taints or []
        ]
        problems = [
            f"{c.type}={c.status} ({one_line(c.message)})"
            for c in conditions
            if (c.type == "Ready" and c.status != "True")
            or (c.type != "Ready" and c.status == "True")
        ]
        details.extend(
            [
                f"Node {n.metadata.name}:",
                f"  Labels: {kv(shown_labels)}",
                f"  Taints: {', '.join(taints) if taints else '<none>'}",
                (
                    f"  Allocatable: cpu={alloc.get('cpu')}, "
                    f"memory={humanize_memory(alloc.get('memory'))}, pods={alloc.get('pods')}"
                ),
                (
                    f"  Capacity: cpu={cap.get('cpu')}, "
                    f"memory={humanize_memory(cap.get('memory'))}, pods={cap.get('pods')}"
                ),
                f"  Problem conditions: {'; '.join(problems) if problems else '<none>'}",
            ]
        )
    head = table(["NAME", "STATUS", "ROLES", "AGE", "VERSION"], summary_rows)
    return head + "\n\n" + "\n".join(details)


def _indent(text: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


ALL_TOOLS = (
    list_namespaces,
    list_pods,
    describe_pod,
    get_pod_logs,
    get_events,
    list_deployments,
    describe_deployment,
    get_nodes,
)
