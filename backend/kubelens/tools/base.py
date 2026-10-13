"""도구 공통 래퍼: 모든 도구 출력은 마스킹 → 절단을 거친다."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kubelens.tools.kube import ReadOnlyKube
from kubelens.tools.masking import mask
from kubelens.tools.truncation import truncate


def finalize(raw: str) -> tuple[str, bool]:
    """마스킹을 먼저 하고 절단한다.

    절단 경계에서 비밀값이 반쪽만 남아 마스킹 패턴을 피하는 일을 막는다.
    """
    return truncate(mask(raw))


@dataclass(frozen=True)
class Tool:
    name: str
    render: Callable[..., str]
    """마스킹·절단 전 원본 출력을 만드는 함수. 첫 인자는 ReadOnlyKube."""
    signature: inspect.Signature = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "signature", inspect.signature(self.render))

    @property
    def description(self) -> str:
        return inspect.getdoc(self.render) or ""

    def __call__(self, kube: ReadOnlyKube, *args: Any, **kwargs: Any) -> str:
        """마스킹·절단이 적용된 문자열을 반환한다."""
        return finalize(self.render(kube, *args, **kwargs))[0]


def tool(fn: Callable[..., str]) -> Tool:
    return Tool(fn.__name__, fn)
