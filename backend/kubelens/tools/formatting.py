"""도구 출력용 텍스트 포맷 헬퍼. kubectl 출력과 비슷한 모양으로 LLM이 읽기 쉽게 만든다."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any


def now() -> datetime:
    return datetime.now(UTC)


def age(ts: datetime | None) -> str:
    """kubectl 스타일 경과 시간 (예: 45s, 12m, 3h, 2d)."""
    if ts is None:
        return "<unknown>"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    seconds = max(int((now() - ts).total_seconds()), 0)
    if seconds < 120:
        return f"{seconds}s"
    if seconds < 7200:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    rendered = [[str(c) for c in headers]] + [[str(c) for c in row] for row in rows]
    widths = [max(len(r[i]) for r in rendered) for i in range(len(headers))]
    lines = [
        "   ".join(cell.ljust(widths[i]) for i, cell in enumerate(r)).rstrip() for r in rendered
    ]
    return "\n".join(lines)


def kv(mapping: Mapping[str, Any] | None, sep: str = ", ") -> str:
    if not mapping:
        return "<none>"
    return sep.join(f"{k}={v}" for k, v in sorted(mapping.items()))


def one_line(text: str | None) -> str:
    return " ".join((text or "").split())


_QUANTITY = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$")
_MULTIPLIER = {
    None: 1,
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "K": 1000,
    "M": 1000**2,
    "G": 1000**3,
    "T": 1000**4,
}


def memory_bytes(quantity: str | None) -> int | None:
    if not quantity:
        return None
    m = _QUANTITY.match(quantity.strip())
    if not m:
        return None
    return int(float(m.group(1)) * _MULTIPLIER[m.group(2)])


def humanize_memory(quantity: str | None) -> str:
    """'16374528Ki' → '16374528Ki (~15.6Gi)'. 비교 판단을 돕기 위해 Gi 환산값을 붙인다."""
    if not quantity:
        return "<none>"
    n = memory_bytes(quantity)
    if n is None or quantity.endswith("Gi"):
        return quantity
    return f"{quantity} (~{n / 1024**3:.1f}Gi)"
