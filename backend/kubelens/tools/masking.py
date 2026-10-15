"""민감정보 마스킹 (기획서 4장 원칙 4, 12장 마스킹 패턴).

도구 출력은 LLM에 전달되기 전에 반드시 이 모듈을 거친다.
"""

import re

MASK = "***"

# 순서가 중요하다: 구체적인 패턴(PEM, URL 자격증명, 토큰 형식)을
# 일반 key=value 패턴보다 먼저 적용한다.
_SECRET_KEYS = (
    r"password|passwd|pwd|token|secret|credentials?|api[_-]?key|access[_-]?key|private[_-]?key"
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # PEM 개인키 블록 (로그 tail로 END가 잘린 경우도 끝까지 가린다)
    (
        re.compile(
            r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        rf"-----BEGIN \g<1>----- {MASK} -----END \g<1>-----",
    ),
    # URL에 박힌 자격증명: postgres://user:pass@host → postgres://user:***@host
    (re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^\s:/@]+:)[^\s@/]+(@)"), rf"\g<1>{MASK}\g<2>"),
    # Authorization: Bearer <token> / Basic <base64>
    (re.compile(r"(Bearer\s+)\S+", re.IGNORECASE), rf"\g<1>{MASK}"),
    (re.compile(r"(Basic\s+)[A-Za-z0-9+/=]{8,}", re.IGNORECASE), rf"\g<1>{MASK}"),
    # JWT (header.payload.signature, header는 항상 eyJ로 시작)
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]*)?"), MASK),
    # 형식이 알려진 토큰: AWS access key id, GitHub, Anthropic, Slack
    (re.compile(r"AKIA[0-9A-Z]{16}"), MASK),
    (re.compile(r"\b(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}"), MASK),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}"), MASK),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), MASK),
    # password=..., token: ..., "api_key": "...", SECRET=... (키 이름은 남기고 값만 가린다)
    (
        re.compile(
            rf"""((?:{_SECRET_KEYS})["']?\s*[=:]\s*["']?)[^\s"',;}}]+""",
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
