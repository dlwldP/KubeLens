"""LangGraph 진단 에이전트 (기획서 8장 그래프 흐름).

질문 → plan → tool_loop(agent ⇄ tools, 최대 8회) → synthesize → validate → 결과
                                                         └ 증거 없는 주장 → synthesize 1회 재시도
"""

from __future__ import annotations

import json
import operator
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal, Protocol, TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from kubelens.agent.prompts import ACT_SYSTEM, PLAN_SYSTEM, SYNTH_SYSTEM
from kubelens.agent.schema import Diagnosis, DiagnosisResult, Evidence
from kubelens.tools import ReadOnlyKube, run_tool

MAX_TOOL_CALLS = 8
MAX_SYNTH_ATTEMPTS = 2  # 최초 1회 + 재생성 1회
MAX_AGENT_TURNS = MAX_TOOL_CALLS + 2  # 도구 없이 도는 턴까지 포함한 안전장치

# SSE 이벤트 타입(기획서 10장)과 같은 이름을 쓴다: tool_start, tool_end, thinking, final, error
EventSink = Callable[[str, dict[str, Any]], None]


class AgentError(RuntimeError):
    """LLM 거부(refusal), 출력 파싱 실패 등 진단을 끝낼 수 없는 오류."""


class DiagnosisCancelled(AgentError):
    """요청자가 떠나는 등으로 진단이 중단됨 (should_stop이 True를 반환)."""


class ChatModel(Protocol):
    def invoke(self, messages: list[AnyMessage]) -> Any: ...


class LLMs(Protocol):
    """그래프가 쓰는 세 가지 LLM 호출. 테스트에서는 가짜로 교체한다."""

    planner: ChatModel  # 텍스트 응답
    actor: ChatModel  # 도구가 바인딩된 모델 → AIMessage(tool_calls)
    synthesizer: ChatModel  # 구조화 출력 → {"raw", "parsed", "parsing_error"}


class AgentState(TypedDict):
    question: str
    namespace: str | None
    plan: str
    messages: Annotated[list[AnyMessage], add_messages]
    evidence: Annotated[list[Evidence], operator.add]
    agent_turns: int
    diagnosis: Diagnosis | None
    validation_errors: list[str]
    synth_attempts: int
    usage: Annotated[dict[str, int], lambda a, b: {k: a.get(k, 0) + b.get(k, 0) for k in a | b}]


def _usage(message: Any) -> dict[str, int]:
    meta = getattr(message, "usage_metadata", None) or {}
    return {
        "input_tokens": int(meta.get("input_tokens", 0)),
        "output_tokens": int(meta.get("output_tokens", 0)),
        "llm_calls": 1,
    }


def _check_refusal(message: Any) -> None:
    meta = getattr(message, "response_metadata", None) or {}
    if meta.get("stop_reason") == "refusal":
        raise AgentError(f"모델이 응답을 거부했습니다: {meta.get('stop_details')}")


def _text(message: AIMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(
        block.get("text", "")
        for block in message.content
        if isinstance(block, dict) and block.get("type") == "text"
    )


def _evidence_block(evidence: list[Evidence]) -> str:
    if not evidence:
        return "(도구 호출 결과 없음)"
    parts = []
    for e in evidence:
        args = json.dumps(e.args, ensure_ascii=False)
        flags = f"status={e.status}" + (", truncated" if e.truncated else "")
        parts.append(f"=== evidence_id: {e.id} | {e.tool_name}({args}) | {flags}\n{e.output}")
    return "\n\n".join(parts)


def validate_diagnosis(diagnosis: Diagnosis, evidence: list[Evidence]) -> list[str]:
    """기획서 4장 원칙 2: 모든 원인 후보는 실제로 존재하는 evidence_ref를 1개 이상 가져야 한다."""
    known = {e.id for e in evidence}
    errors = []
    for i, cause in enumerate(diagnosis.root_causes, start=1):
        if not cause.evidence_refs:
            errors.append(f"root_causes[{i}] '{cause.title}': evidence_refs가 비어 있음")
            continue
        unknown = [ref for ref in cause.evidence_refs if ref not in known]
        if unknown:
            errors.append(
                f"root_causes[{i}] '{cause.title}': 존재하지 않는 evidence_id {unknown} "
                f"(사용 가능: {sorted(known)})"
            )
    return errors


def build_graph(
    kube: ReadOnlyKube,
    llms: LLMs,
    on_event: EventSink | None = None,
    *,
    deadline: float | None = None,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    run_info: dict[str, Any] | None = None,
) -> Any:
    """deadline(clock 기준 시각)이 지나면 도구 수집을 멈추고 모은 증거로 보고서를 쓴다.
    should_stop()이 True면 다음 단계 시작 전에 DiagnosisCancelled를 던진다."""
    emit: EventSink = on_event or (lambda _type, _data: None)
    info = run_info if run_info is not None else {}
    info.setdefault("timed_out", False)

    def check_cancel() -> None:
        if should_stop is not None and should_stop():
            raise DiagnosisCancelled("진단이 취소되었습니다")

    def timed_out() -> bool:
        if deadline is None or clock() < deadline:
            return False
        if not info["timed_out"]:
            info["timed_out"] = True
            emit(
                "thinking", {"message": "시간 상한 도달 — 지금까지 모은 증거로 보고서를 작성합니다"}
            )
        return True

    def plan(state: AgentState) -> dict[str, Any]:
        check_cancel()
        emit("thinking", {"message": "조사 계획 수립 중"})
        scope = f"\n(선택된 네임스페이스: {state['namespace']})" if state["namespace"] else ""
        response = llms.planner.invoke(
            [
                SystemMessage(PLAN_SYSTEM.format(max_calls=MAX_TOOL_CALLS)),
                HumanMessage(state["question"] + scope),
            ]
        )
        _check_refusal(response)
        plan_text = _text(response).strip()
        emit("thinking", {"message": "조사 계획", "plan": plan_text})
        kickoff = HumanMessage(
            f"질문: {state['question']}{scope}\n\n조사 계획:\n{plan_text}\n\n"
            "계획에 따라 도구로 증거를 수집해라."
        )
        return {"plan": plan_text, "messages": [kickoff], "usage": _usage(response)}

    def agent(state: AgentState) -> dict[str, Any]:
        check_cancel()
        remaining = MAX_TOOL_CALLS - len(state["evidence"])
        emit("thinking", {"message": f"증거 수집 중 (남은 도구 호출 {remaining}회)"})
        response = llms.actor.invoke(
            [SystemMessage(ACT_SYSTEM.format(max_calls=MAX_TOOL_CALLS)), *state["messages"]]
        )
        _check_refusal(response)
        return {
            "messages": [response],
            "agent_turns": state["agent_turns"] + 1,
            "usage": _usage(response),
        }

    def tools(state: AgentState) -> dict[str, Any]:
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        used = len(state["evidence"])
        new_evidence: list[Evidence] = []
        replies: list[ToolMessage] = []
        for call in last.tool_calls:
            check_cancel()
            if timed_out():
                replies.append(
                    ToolMessage(
                        "ERROR: 진단 시간 상한을 넘어 실행하지 않았다.",
                        tool_call_id=call["id"],
                        status="error",
                    )
                )
                continue
            if used + len(new_evidence) >= MAX_TOOL_CALLS:
                # tool_use에는 반드시 tool_result가 따라와야 하므로 실행하지 않은 호출에도 응답한다.
                replies.append(
                    ToolMessage(
                        f"ERROR: 도구 호출 상한({MAX_TOOL_CALLS}회)을 넘어 실행하지 않았다.",
                        tool_call_id=call["id"],
                        status="error",
                    )
                )
                continue
            evidence_id = f"tc_{used + len(new_evidence) + 1}"
            emit("tool_start", {"id": evidence_id, "tool_name": call["name"], "args": call["args"]})
            result = run_tool(kube, call["name"], call["args"])
            ev = Evidence(
                id=evidence_id,
                tool_name=result.tool_name,
                args=result.args,
                output=result.output,
                truncated=result.truncated,
                duration_ms=result.duration_ms,
                status=result.status,
            )
            new_evidence.append(ev)
            emit(
                "tool_end",
                {
                    "id": ev.id,
                    "tool_name": ev.tool_name,
                    "status": ev.status,
                    "duration_ms": ev.duration_ms,
                    "truncated": ev.truncated,
                    "args": ev.args,
                    # 마스킹·절단 후 출력. API가 DB에 저장한다 (SSE로는 보내지 않음)
                    "output": ev.output,
                },
            )
            replies.append(
                ToolMessage(
                    f"[evidence_id: {ev.id}]\n{ev.output}",
                    tool_call_id=call["id"],
                    status="success" if ev.status == "ok" else "error",
                )
            )
        return {"messages": replies, "evidence": new_evidence}

    def synthesize(state: AgentState) -> dict[str, Any]:
        check_cancel()
        attempt = state["synth_attempts"] + 1
        emit("thinking", {"message": "진단 보고서 작성 중", "attempt": attempt})
        prompt = (
            f"질문: {state['question']}\n\n"
            f"사용 가능한 evidence_id: {[e.id for e in state['evidence']]}\n\n"
            f"증거:\n{_evidence_block(state['evidence'])}"
        )
        if state["validation_errors"]:
            prompt += (
                "\n\n이전 보고서가 검증에 실패했다. 아래 문제를 고쳐 다시 작성해라. "
                "근거를 댈 수 없는 원인 후보는 빼라.\n- " + "\n- ".join(state["validation_errors"])
            )
        out = llms.synthesizer.invoke([SystemMessage(SYNTH_SYSTEM), HumanMessage(prompt)])
        raw = out.get("raw")
        _check_refusal(raw)
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            raise AgentError(f"진단 출력 파싱 실패: {out.get('parsing_error')}")
        return {"diagnosis": out["parsed"], "synth_attempts": attempt, "usage": _usage(raw)}

    def validate(state: AgentState) -> dict[str, Any]:
        assert state["diagnosis"] is not None
        return {"validation_errors": validate_diagnosis(state["diagnosis"], state["evidence"])}

    def after_agent(state: AgentState) -> Literal["tools", "synthesize"]:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and last.tool_calls and not timed_out():
            return "tools"
        return "synthesize"

    def after_tools(state: AgentState) -> Literal["agent", "synthesize"]:
        if (
            len(state["evidence"]) >= MAX_TOOL_CALLS
            or state["agent_turns"] >= MAX_AGENT_TURNS
            or timed_out()
        ):
            return "synthesize"
        return "agent"

    def after_validate(state: AgentState) -> Literal["synthesize", "__end__"]:
        if state["validation_errors"] and state["synth_attempts"] < MAX_SYNTH_ATTEMPTS:
            return "synthesize"
        return END

    g = StateGraph(AgentState)
    g.add_node("plan", plan)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("synthesize", synthesize)
    g.add_node("validate", validate)
    g.add_edge(START, "plan")
    g.add_edge("plan", "agent")
    g.add_conditional_edges("agent", after_agent)
    g.add_conditional_edges("tools", after_tools)
    g.add_edge("synthesize", "validate")
    g.add_conditional_edges("validate", after_validate)
    return g.compile()


def diagnose(
    question: str,
    kube: ReadOnlyKube,
    llms: LLMs,
    namespace: str | None = None,
    on_event: EventSink | None = None,
    *,
    timeout_s: float | None = None,
    should_stop: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> DiagnosisResult:
    """timeout_s: 증거 수집 시간 상한(초). 넘으면 모은 증거만으로 보고서를 쓰고 timed_out=True."""
    emit: EventSink = on_event or (lambda _type, _data: None)
    run_info: dict[str, Any] = {}
    graph = build_graph(
        kube,
        llms,
        on_event,
        deadline=None if timeout_s is None else clock() + timeout_s,
        should_stop=should_stop,
        clock=clock,
        run_info=run_info,
    )
    initial: AgentState = {
        "question": question,
        "namespace": namespace,
        "plan": "",
        "messages": [],
        "evidence": [],
        "agent_turns": 0,
        "diagnosis": None,
        "validation_errors": [],
        "synth_attempts": 0,
        "usage": {},
    }
    try:
        state = graph.invoke(initial, {"recursion_limit": 50})
    except Exception as exc:
        emit("error", {"message": str(exc)})
        raise
    result = DiagnosisResult(
        question=question,
        diagnosis=state["diagnosis"],
        evidence=state["evidence"],
        valid=not state["validation_errors"],
        validation_errors=state["validation_errors"],
        synth_attempts=state["synth_attempts"],
        usage=state["usage"],
        timed_out=run_info["timed_out"],
    )
    emit("final", result.to_dict())
    return result
