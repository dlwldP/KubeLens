"""가짜 LLM (API 키 불필요). 단위 테스트와 CI/e2e(KUBELENS_LLM_FACTORY)에서 쓴다.

실제 모델이 아니므로 진단 품질 검증에는 쓸 수 없다. 그래프·API·UI 배선 확인용이다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, AnyMessage

from kubelens.agent import Diagnosis, NextAction, RootCause


def tool_call(name: str, args: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


@dataclass
class ScriptedActor:
    """턴마다 미리 정한 tool_calls를 돌려주고, 다 쓰면 도구 없이 끝낸다."""

    turns: list[list[dict[str, Any]]]
    seen: list[list[AnyMessage]] = field(default_factory=list)

    def invoke(self, messages: list[AnyMessage]) -> AIMessage:
        self.seen.append(list(messages))
        if self.turns:
            return AIMessage(content="", tool_calls=self.turns.pop(0))
        return AIMessage(content="조사 완료")


@dataclass
class Planner:
    def invoke(self, messages: list[AnyMessage]) -> AIMessage:
        return AIMessage(content="1. 비정상 파드 확인\n2. 최근 이벤트 확인")


@dataclass
class ScriptedSynth:
    outputs: list[Diagnosis | Callable[[str], Diagnosis]]
    prompts: list[str] = field(default_factory=list)

    def invoke(self, messages: list[AnyMessage]) -> dict[str, Any]:
        prompt = str(messages[-1].content)
        self.prompts.append(prompt)
        out = self.outputs.pop(0)
        parsed = out(prompt) if callable(out) else out
        return {"raw": AIMessage(content=""), "parsed": parsed, "parsing_error": None}


@dataclass
class FakeLLMs:
    planner: Any
    actor: Any
    synthesizer: Any


def _cite_all(prompt: str) -> Diagnosis:
    ids = re.findall(r"evidence_id: (tc_\d+)", prompt)
    return Diagnosis(
        summary="[scripted LLM] 비정상 파드와 최근 이벤트를 조회했습니다. 실제 진단 아님.",
        root_causes=[
            RootCause(
                title="[scripted] 비정상 파드 발견",
                confidence="low",
                explanation="가짜 LLM이 만든 결과입니다. 증거 링크 배선 확인용입니다.",
                evidence_refs=ids[:2] or ["tc_1"],
            )
        ],
        next_actions=[
            NextAction(
                description="비정상 파드 이벤트 확인",
                command="kubectl get events -A --sort-by=.lastTimestamp",
            )
        ],
    )


class ScriptedLLMs:
    planner = Planner()

    def __init__(self) -> None:
        self.actor = _ResettingActor()
        self.synthesizer = _CitingSynth()


class _ResettingActor:
    """plan 직후 첫 호출마다 대본을 처음부터 다시 시작한다."""

    def invoke(self, messages: list[AnyMessage]) -> AIMessage:
        turn = sum(1 for m in messages if isinstance(m, AIMessage))
        script = [
            [tool_call("list_pods", {"namespace": "*", "only_unhealthy": True}, f"s{turn}a")],
            [tool_call("get_events", {"namespace": "*", "limit": 20}, f"s{turn}b")],
        ]
        if turn < len(script):
            return AIMessage(content="", tool_calls=script[turn])
        return AIMessage(content="조사 완료")


class _CitingSynth:
    def invoke(self, messages: list[AnyMessage]) -> dict[str, Any]:
        return {
            "raw": AIMessage(content=""),
            "parsed": _cite_all(str(messages[-1].content)),
            "parsing_error": None,
        }


def scripted_llms(model: str | None = None) -> ScriptedLLMs:
    """KUBELENS_LLM_FACTORY=tests.fake_llms:scripted_llms"""
    return ScriptedLLMs()
