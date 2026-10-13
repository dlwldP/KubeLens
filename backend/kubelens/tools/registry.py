"""도구 allowlist와 실행기. 에이전트(Phase 2)와 API(Phase 4)는 run_tool로만 도구를 호출한다."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from kubernetes.client.exceptions import ApiException

from kubelens.tools.base import Tool, finalize
from kubelens.tools.k8s import ALL_TOOLS
from kubelens.tools.kube import ReadOnlyKube

# 기획서 8장 도구 목록. 여기에 없는 이름은 실행되지 않는다.
TOOLS: dict[str, Tool] = {t.name: t for t in ALL_TOOLS}

ToolStatus = Literal["ok", "error"]


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    args: dict[str, Any]
    output: str
    """마스킹·절단이 끝난 출력 (ToolCall.output_text로 저장되고 LLM에 전달된다)."""
    truncated: bool
    duration_ms: int
    status: ToolStatus


def _api_error_text(exc: ApiException) -> str:
    message = exc.reason or ""
    if exc.body:
        try:
            message = json.loads(exc.body).get("message", message)
        except (ValueError, AttributeError):
            message = str(exc.body)
    return f"ERROR {exc.status} {exc.reason}: {message}"


def run_tool(kube: ReadOnlyKube, name: str, args: dict[str, Any] | None = None) -> ToolResult:
    """allowlist 도구를 실행한다. 예외는 삼키고 status="error" 결과로 돌려준다."""
    args = dict(args or {})
    started = time.perf_counter()
    status: ToolStatus = "error"
    t = TOOLS.get(name)
    if t is None:
        raw = f"ERROR: unknown tool '{name}'. Allowed: {', '.join(TOOLS)}"
    else:
        try:
            t.signature.bind(kube, **args)
        except TypeError as exc:
            raw = f"ERROR: invalid arguments for {name}: {exc}"
        else:
            try:
                raw = t.render(kube, **args)
                status = "ok"
            except ApiException as exc:
                raw = _api_error_text(exc)
            except (
                Exception
            ) as exc:  # 연결 실패 등. 에이전트가 계속 진행할 수 있게 문자열로 돌려준다.
                raw = f"ERROR: {type(exc).__name__}: {exc}"
    output, truncated = finalize(raw)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ToolResult(name, args, output, truncated, duration_ms, status)
