"""Read-only Kubernetes 접근 계층 (기획서 4장 원칙 1: Read-only by construction).

도구는 kubernetes client의 Api 객체를 직접 만지지 않고 이 클래스만 사용한다.
여기에는 get/list 계열 메서드만 정의되어 있으며, 쓰기·exec·secret 조회 경로는 존재하지 않는다.
RBAC(infra/rbac.yaml)이 두 번째 방어선이다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kubernetes import client, config

ALL_NAMESPACES = "*"

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KUBECONFIG = _REPO_ROOT / "infra" / "kubelens.kubeconfig"


def default_kubeconfig_path() -> Path:
    """KUBELENS_KUBECONFIG 환경변수 또는 infra/kubelens.kubeconfig (read-only SA)."""
    return Path(os.environ.get("KUBELENS_KUBECONFIG", DEFAULT_KUBECONFIG))


class ReadOnlyKube:
    """allowlist된 조회 메서드만 노출하는 래퍼. 원본 Api 객체로의 우회 경로를 두지 않는다."""

    __slots__ = ("_core", "_apps")

    def __init__(self, core: Any, apps: Any) -> None:
        self._core = core
        self._apps = apps

    @classmethod
    def from_kubeconfig(
        cls, path: str | os.PathLike[str] | None = None, context: str | None = None
    ) -> ReadOnlyKube:
        kubeconfig = Path(path) if path is not None else default_kubeconfig_path()
        api_client = config.new_client_from_config(config_file=str(kubeconfig), context=context)
        return cls(client.CoreV1Api(api_client), client.AppsV1Api(api_client))

    # --- core/v1 ---------------------------------------------------------

    def list_namespace(self) -> Any:
        return self._core.list_namespace()

    def list_pod(self, namespace: str, label_selector: str | None = None) -> Any:
        kwargs = {"label_selector": label_selector} if label_selector else {}
        if namespace == ALL_NAMESPACES:
            return self._core.list_pod_for_all_namespaces(**kwargs)
        return self._core.list_namespaced_pod(namespace, **kwargs)

    def read_pod(self, namespace: str, name: str) -> Any:
        return self._core.read_namespaced_pod(name, namespace)

    def read_pod_log(
        self,
        namespace: str,
        name: str,
        *,
        container: str | None,
        previous: bool,
        tail_lines: int,
    ) -> str:
        kwargs: dict[str, Any] = {"previous": previous, "tail_lines": tail_lines}
        if container:
            kwargs["container"] = container
        return self._core.read_namespaced_pod_log(name, namespace, **kwargs)

    def list_event(self, namespace: str) -> Any:
        if namespace == ALL_NAMESPACES:
            return self._core.list_event_for_all_namespaces()
        return self._core.list_namespaced_event(namespace)

    def list_node(self) -> Any:
        return self._core.list_node()

    # --- apps/v1 ---------------------------------------------------------

    def list_deployment(self, namespace: str) -> Any:
        if namespace == ALL_NAMESPACES:
            return self._apps.list_deployment_for_all_namespaces()
        return self._apps.list_namespaced_deployment(namespace)

    def read_deployment(self, namespace: str, name: str) -> Any:
        return self._apps.read_namespaced_deployment(name, namespace)

    def list_replica_set(self, namespace: str, label_selector: str | None = None) -> Any:
        kwargs = {"label_selector": label_selector} if label_selector else {}
        return self._apps.list_namespaced_replica_set(namespace, **kwargs)
