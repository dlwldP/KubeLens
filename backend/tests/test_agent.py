"""가짜 LLM으로 LangGraph 흐름을 검증한다 (API 키·클러스터 불필요)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

from kubelens.agent import (
    MAX_TOOL_CALLS,
    AgentError,
    Diagnosis,
    Evidence,
    RootCause,
    diagnose,
    validate_diagnosis,
)
from kubelens.agent.tool_specs import all_tool_specs
from kubelens.tools import TOOLS
from tests.fake_llms import FakeLLMs, Planner, ScriptedActor, ScriptedSynth
from tests.fake_llms import tool_call as _call
from tests.fakes import NS

S1_POD = "payment-api-7d9f-abcde"


def s1_diagnosis(refs: list[str]) -> Diagnosis:
    return Diagnosis(
        summary="payment-api가 DB_HOST 환경변수 누락으로 exit 1 후 CrashLoopBackOff",
        root_causes=[
            RootCause(
                title="필수 환경변수 DB_HOST 누락",
                confidence="high",
                explanation="직전 로그에 'DB_HOST is not set', exit code 1",
                evidence_refs=refs,
            )
        ],
        next_actions=[],
    )


S1_TURNS = [
    [_call("list_pods", {"namespace": NS, "label_selector": "app=payment-api"}, "a1")],
    [
        _call("describe_pod", {"namespace": NS, "name": S1_POD}, "b1"),
        _call("get_pod_logs", {"namespace": NS, "name": S1_POD, "previous": True}, "b2"),
    ],
]


def test_tool_specs_cover_allowlist() -> None:
    specs = {s["name"]: s for s in all_tool_specs()}
    assert set(specs) == set(TOOLS)
    logs = specs["get_pod_logs"]["input_schema"]
    assert logs["required"] == ["namespace", "name"]
    assert logs["properties"]["previous"] == {"type": "boolean", "default": False}
    assert "kube" not in logs["properties"]
    assert specs["get_nodes"]["input_schema"]["properties"] == {}


def test_s1_happy_path_collects_evidence_and_validates(kube) -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    actor = ScriptedActor([list(t) for t in S1_TURNS])
    synth = ScriptedSynth([s1_diagnosis(["tc_3"])])
    result = diagnose(
        "payment-api가 계속 재시작돼",
        kube,
        FakeLLMs(Planner(), actor, synth),
        on_event=lambda t, d: events.append((t, d)),
    )
    assert result.valid
    assert [e.id for e in result.evidence] == ["tc_1", "tc_2", "tc_3"]
    assert [e.tool_name for e in result.evidence] == ["list_pods", "describe_pod", "get_pod_logs"]
    logs = result.evidence[2]
    assert "DB_HOST" in logs.output
    assert "kl-fake-0000000000000000" not in logs.output  # 마스킹된 출력만 LLM에 간다
    # 합성 단계 프롬프트에 evidence id와 원본(마스킹된) 출력이 들어간다
    assert "evidence_id: tc_3" in synth.prompts[0]
    assert "kl-fake-0000000000000000" not in synth.prompts[0]
    # 도구 결과는 evidence_id와 함께 같은 tool_call_id로 회신된다
    tool_msgs = [m for m in actor.seen[-1] if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_msgs] == ["a1", "b1", "b2"]
    assert tool_msgs[0].content.startswith("[evidence_id: tc_1]")
    types = [t for t, _ in events]
    assert types[0] == "thinking"
    assert types.count("tool_start") == 3 and types.count("tool_end") == 3
    assert types[-1] == "final"
    assert result.usage["llm_calls"] == 1 + 3 + 1  # plan + agent 3턴 + synth


def test_tool_budget_is_capped_at_8(kube) -> None:
    greedy = [[_call("get_nodes", {}, f"n{i}_{j}") for j in range(3)] for i in range(10)]
    actor = ScriptedActor(greedy)
    synth = ScriptedSynth([s1_diagnosis(["tc_1"])])
    result = diagnose("전체 점검", kube, FakeLLMs(Planner(), actor, synth))
    assert len(result.evidence) == MAX_TOOL_CALLS
    # 3번째 턴의 3번째 호출(9번째)은 실행되지 않고 오류 tool_result로 회신된다
    assert len(actor.seen) == 3
    assert result.valid


def test_missing_evidence_triggers_one_regeneration(kube) -> None:
    actor = ScriptedActor([list(t) for t in S1_TURNS])
    synth = ScriptedSynth([s1_diagnosis([]), s1_diagnosis(["tc_2", "tc_3"])])
    result = diagnose("왜 재시작돼?", kube, FakeLLMs(Planner(), actor, synth))
    assert result.valid
    assert result.synth_attempts == 2
    assert "검증에 실패" in synth.prompts[1]
    assert "evidence_refs가 비어 있음" in synth.prompts[1]


def test_still_invalid_after_retry_is_reported(kube) -> None:
    actor = ScriptedActor([list(t) for t in S1_TURNS])
    synth = ScriptedSynth([s1_diagnosis(["tc_99"]), s1_diagnosis(["tc_99"])])
    result = diagnose("왜?", kube, FakeLLMs(Planner(), actor, synth))
    assert not result.valid
    assert result.synth_attempts == 2
    assert "tc_99" in result.validation_errors[0]


def test_refusal_raises_agent_error(kube) -> None:
    class Refuser:
        def invoke(self, messages: list[AnyMessage]) -> AIMessage:
            return AIMessage(content="", response_metadata={"stop_reason": "refusal"})

    with pytest.raises(AgentError):
        diagnose("?", kube, FakeLLMs(Refuser(), ScriptedActor([]), ScriptedSynth([])))


def test_parsing_error_raises_agent_error(kube) -> None:
    class BadSynth:
        def invoke(self, messages: list[AnyMessage]) -> dict[str, Any]:
            return {"raw": AIMessage(content="{"), "parsed": None, "parsing_error": "bad json"}

    with pytest.raises(AgentError, match="파싱"):
        diagnose("?", kube, FakeLLMs(Planner(), ScriptedActor([]), BadSynth()))


def test_validate_diagnosis_allows_no_root_causes() -> None:
    healthy = Diagnosis(summary="문제 없음", root_causes=[], next_actions=[])
    assert validate_diagnosis(healthy, []) == []


def test_validate_diagnosis_flags_unknown_refs() -> None:
    ev = [Evidence("tc_1", "get_nodes", {}, "", False, 1, "ok")]
    errors = validate_diagnosis(s1_diagnosis(["tc_1", "tc_7"]), ev)
    assert len(errors) == 1 and "tc_7" in errors[0]
