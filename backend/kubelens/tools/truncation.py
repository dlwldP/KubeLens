"""도구 출력 절단 (기획서 4장 원칙 3: 도구 결과는 LLM에 넣기 전에 절단한다)."""

MAX_OUTPUT_CHARS = 4000
# 표 헤더/앞부분과 로그 끝부분(보통 장애 원인이 있는 곳)을 모두 살리기 위해 앞뒤를 남긴다.
_HEAD_RATIO = 0.3


def truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    """limit 이하로 자른 문자열과 절단 여부를 반환한다. 결과 길이는 항상 limit 이하."""
    if len(text) <= limit:
        return text, False
    omitted = len(text) - limit  # 마커 길이만큼 더 잘리지만 대략적인 수치로 충분하다
    marker = f"\n... [truncated: ~{omitted} chars omitted] ...\n"
    budget = max(limit - len(marker), 0)
    head = int(budget * _HEAD_RATIO)
    tail = budget - head
    return text[:head] + marker + (text[-tail:] if tail else ""), True
