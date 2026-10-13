"""민감정보 마스킹 (기획서 4장 원칙 4, 12장 마스킹 패턴).

도구 출력은 LLM에 전달되기 전에 반드시 이 모듈을 거친다.
"""

import re

MASK = "***"

# 순서가 중요하다: 더 구체적인 패턴(Bearer, JWT, AWS 키)을 key=value 패턴보다 먼저 적용한다.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Authorization: Bearer <token>
    (re.compile(r"(Bearer\s+)\S+", re.IGNORECASE), rf"\g<1>{MASK}"),
    # JWT (header.payload.signature, header는 항상 eyJ로 시작)
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?"), MASK),
    # AWS access key id
    (re.compile(r"AKIA[0-9A-Z]{16}"), MASK),
    # password=..., token: ..., "api_key": "...", SECRET=... (키 이름은 남기고 값만 가린다)
    (
        re.compile(
            r"""((?:password|passwd|token|secret|api[_-]?key)["']?\s*[=:]\s*["']?)"""
            r"""[^\s"',;}]+""",
            re.IGNORECASE,
        ),
        rf"\g<1>{MASK}",
    ),
]


def mask(text: str) -> str:
    """토큰·키 패턴을 마스킹한 문자열을 반환한다."""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text
