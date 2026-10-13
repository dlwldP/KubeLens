"""Claude(langchain-anthropic) 기반 LLM 구성 (기획서 7장: claude-sonnet-5-5)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from langchain_anthropic import ChatAnthropic

from kubelens.agent.schema import Diagnosis
from kubelens.agent.tool_specs import all_tool_specs

DEFAULT_MODEL = "claude-sonnet-5-5"
# 안전 분류기가 요청을 거부(stop_reason=refusal)하면 서버가 대체 모델로 자동 재시도한다.
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _chat(model: str, effort: str) -> ChatAnthropic:
    # temperature 등 샘플링 파라미터는 이 모델에서 기본값 외 사용 불가 → 지정하지 않는다.
    # thinking도 지정하지 않으면 adaptive로 동작한다.
    return ChatAnthropic(
        model=model,
        max_tokens=16000,
        effort=effort,
        betas=[FALLBACK_BETA],
        model_kwargs={"fallbacks": "default"},
        max_retries=2,
        timeout=300,
    )


@dataclass(frozen=True)
class AnthropicLLMs:
    planner: Any
    actor: Any
    synthesizer: Any


def anthropic_llms(model: str | None = None) -> AnthropicLLMs:
    model = model or os.environ.get("KUBELENS_MODEL", DEFAULT_MODEL)
    return AnthropicLLMs(
        planner=_chat(model, "low"),
        # tool_choice any/tool은 이 모델에서 400 → auto(기본값)로 두고 프롬프트로 유도한다.
        actor=_chat(model, "medium").bind_tools(all_tool_specs()),
        # 강제 tool call 대신 output_config.format(json_schema) 구조화 출력을 쓴다.
        synthesizer=_chat(model, "medium").with_structured_output(
            Diagnosis, method="json_schema", include_raw=True
        ),
    )
