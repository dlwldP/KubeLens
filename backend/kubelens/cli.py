"""CLI: python -m kubelens ask "질문" (기획서 14장 Phase 2 완료 조건)."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from kubelens.agent import AgentError, DiagnosisResult, diagnose
from kubelens.tools import ReadOnlyKube

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INVALID = 2  # 증거 검증 실패


def _print_event(event_type: str, data: dict[str, Any]) -> None:
    """도구 트레이스를 실시간으로 stderr에 출력한다 (stdout은 결과 전용)."""
    if event_type == "tool_start":
        args = ", ".join(f"{k}={v!r}" for k, v in data["args"].items())
        print(f"  ▶ {data['id']} {data['tool_name']}({args})", file=sys.stderr, flush=True)
    elif event_type == "tool_end":
        mark = "✔" if data["status"] == "ok" else "✖"
        extra = " (truncated)" if data["truncated"] else ""
        print(
            f"  {mark} {data['id']} {data['status']} {data['duration_ms']}ms{extra}",
            file=sys.stderr,
            flush=True,
        )
    elif event_type == "thinking":
        print(f"… {data['message']}", file=sys.stderr, flush=True)
    elif event_type == "error":
        print(f"✖ {data['message']}", file=sys.stderr, flush=True)


def _render(result: DiagnosisResult) -> str:
    d = result.diagnosis
    lines = [f"질문: {result.question}", ""]
    if d is None:
        return "\n".join([*lines, "(진단 없음)"])
    lines += ["요약", f"  {d.summary}", "", "원인 후보"]
    if not d.root_causes:
        lines.append("  (발견된 문제 없음)")
    for i, c in enumerate(d.root_causes, start=1):
        lines.append(f"  {i}. [{c.confidence}] {c.title}  근거: {', '.join(c.evidence_refs)}")
        lines.append(f"     {c.explanation}")
    lines += ["", "다음 조치 (제안만, 실행하지 않음)"]
    for a in d.next_actions:
        lines.append(f"  - {a.description}")
        if a.command:
            lines.append(f"      $ {a.command}")
    lines += ["", "증거"]
    for e in result.evidence:
        args = json.dumps(e.args, ensure_ascii=False)
        flag = " truncated" if e.truncated else ""
        lines.append(f"  {e.id} {e.tool_name}({args}) {e.status}{flag} {e.duration_ms}ms")
    u = result.usage
    lines.append(
        f"\nLLM 호출 {u.get('llm_calls', 0)}회, 입력 {u.get('input_tokens', 0)} / "
        f"출력 {u.get('output_tokens', 0)} 토큰, 도구 호출 {len(result.evidence)}회"
    )
    if result.timed_out:
        lines.append("⏱ 시간 상한에 도달해 일부 증거만으로 작성한 보고서입니다.")
    if not result.valid:
        lines += ["", "⚠ 증거 검증 실패:", *(f"  - {e}" for e in result.validation_errors)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kubelens", description="read-only Kubernetes 진단")
    sub = parser.add_subparsers(dest="command", required=True)
    ask = sub.add_parser("ask", help="자연어로 진단 질문")
    ask.add_argument("question")
    ask.add_argument("-n", "--namespace", default=None, help="대상 네임스페이스 (선택)")
    ask.add_argument("--kubeconfig", default=None, help="기본값: infra/kubelens.kubeconfig")
    ask.add_argument("--context", default=None)
    ask.add_argument("--model", default=None, help="기본값: KUBELENS_MODEL 또는 claude-sonnet-5-5")
    ask.add_argument(
        "--timeout",
        type=float,
        default=180,
        help="증거 수집 시간 상한(초). 넘으면 모은 증거로 보고서 작성. 0이면 무제한 (기본 180)",
    )
    ask.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    ask.add_argument("-q", "--quiet", action="store_true", help="실시간 트레이스 숨김")
    args = parser.parse_args(argv)

    from kubelens.agent.llm import anthropic_llms  # langchain import는 실제 실행 시에만

    try:
        kube = ReadOnlyKube.from_kubeconfig(args.kubeconfig, args.context)
        result = diagnose(
            args.question,
            kube,
            anthropic_llms(args.model),
            namespace=args.namespace,
            on_event=None if args.quiet else _print_event,
            timeout_s=args.timeout or None,
        )
    except AgentError as exc:
        print(f"진단 실패: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(_render(result))
    return EXIT_OK if result.valid else EXIT_INVALID
