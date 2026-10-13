"""평가 하네스 (기획서 13장, Phase 3).

chaos/expected.yaml의 시나리오 S1~S6을 실제 에이전트로 진단하고, 1순위 원인 후보에 정답 키워드가
있는지 채점해 정확도 표를 만든다. 실행: `make eval` (kind 클러스터 + ANTHROPIC_API_KEY 필요)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from kubelens.agent import AgentError, DiagnosisResult

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_PATH = REPO_ROOT / "chaos" / "expected.yaml"
RESULTS_DIR = REPO_ROOT / "eval" / "results"

# 입력/출력 100만 토큰당 달러 (Claude API 정가)
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

# 기획서 13장 목표: 6개 중 5개 이상 1순위 적중 + 모든 원인 후보에 증거 링크
TARGET_HITS = 5

DiagnoseFn = Callable[[str, str | None], DiagnosisResult]


@dataclass
class ScenarioScore:
    id: str
    question: str
    hit: bool = False
    valid: bool = False
    top_cause: str = ""
    missing_groups: list[list[str]] = field(default_factory=list)
    leaked: list[str] = field(default_factory=list)
    root_causes: int = 0
    tool_calls: int = 0
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: str | None = None


def load_scenarios(path: Path = EXPECTED_PATH, only: list[str] | None = None) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text())
    if only:
        wanted = {s.upper() for s in only}
        data["scenarios"] = [s for s in data["scenarios"] if s["id"].upper() in wanted]
        missing = wanted - {s["id"] for s in data["scenarios"]}
        if missing:
            raise SystemExit(f"알 수 없는 시나리오: {sorted(missing)}")
    return data


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES_PER_MTOK.get(model, PRICES_PER_MTOK["claude-sonnet-5-5"])
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def score(scenario: dict[str, Any], result: DiagnosisResult, model: str) -> ScenarioScore:
    s = ScenarioScore(id=scenario["id"], question=scenario["question"])
    s.valid = result.valid
    s.tool_calls = len(result.evidence)
    s.input_tokens = result.usage.get("input_tokens", 0)
    s.output_tokens = result.usage.get("output_tokens", 0)
    s.cost_usd = cost_usd(model, s.input_tokens, s.output_tokens)

    d = result.diagnosis
    causes = d.root_causes if d else []
    s.root_causes = len(causes)
    top_text = ""
    if causes:
        s.top_cause = causes[0].title
        top_text = f"{causes[0].title}\n{causes[0].explanation}".lower()
    s.missing_groups = [
        group
        for group in scenario.get("must_match", [])
        if not any(alt.lower() in top_text for alt in group)
    ]
    # 최종 출력과 LLM에 전달된 도구 출력 어디에도 비밀값이 있으면 안 된다
    everything = json.dumps(result.to_dict(), ensure_ascii=False) + "".join(
        e.output for e in result.evidence
    )
    s.leaked = [x for x in scenario.get("must_not_appear", []) if x in everything]
    s.hit = bool(causes) and s.valid and not s.missing_groups and not s.leaked
    return s


def run(
    scenarios: dict[str, Any],
    diagnose_fn: DiagnoseFn,
    model: str,
    log: Callable[[str], None] = lambda m: print(m, file=sys.stderr, flush=True),
) -> list[ScenarioScore]:
    scores = []
    for sc in scenarios["scenarios"]:
        log(f"[{sc['id']}] {sc['question']}")
        started = time.perf_counter()
        try:
            result = diagnose_fn(sc["question"], scenarios.get("namespace"))
            s = score(sc, result, model)
        except AgentError as exc:
            s = ScenarioScore(id=sc["id"], question=sc["question"], error=str(exc))
        s.duration_s = round(time.perf_counter() - started, 1)
        mark = "✅" if s.hit else "❌"
        log(f"[{sc['id']}] {mark} {s.top_cause or s.error or '(원인 후보 없음)'} ({s.duration_s}s)")
        scores.append(s)
    return scores


def summarize(scores: list[ScenarioScore]) -> dict[str, Any]:
    n = len(scores) or 1
    hits = sum(s.hit for s in scores)
    evidence_ok = all(s.valid for s in scores if s.error is None) and not any(
        s.error for s in scores
    )
    return {
        "scenarios": len(scores),
        "hits": hits,
        "evidence_ok": evidence_ok,
        "avg_tool_calls": round(sum(s.tool_calls for s in scores) / n, 1),
        "avg_duration_s": round(sum(s.duration_s for s in scores) / n, 1),
        "avg_cost_usd": round(sum(s.cost_usd for s in scores) / n, 4),
        "total_cost_usd": round(sum(s.cost_usd for s in scores), 4),
    }


def render_markdown(scores: list[ScenarioScore], model: str, run_at: str) -> str:
    summary = summarize(scores)
    lines = [
        f"# KubeLens 평가 결과 ({run_at})",
        "",
        f"모델: `{model}`",
        "",
        "| # | 1순위 적중 | 증거 검증 | 1순위 원인 후보 | 도구 호출 | 응답 시간 | 비용 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in scores:
        note = s.top_cause or (f"오류: {s.error}" if s.error else "(없음)")
        if s.missing_groups:
            note += f" — 누락: {' / '.join('|'.join(g) for g in s.missing_groups)}"
        if s.leaked:
            note += f" — 비밀값 노출: {s.leaked}"
        note = note.replace("|", "\\|")
        lines.append(
            f"| {s.id} | {'✅' if s.hit else '❌'} | {'✅' if s.valid else '❌'} | {note} | "
            f"{s.tool_calls} | {s.duration_s}s | ${s.cost_usd:.4f} |"
        )
    total = summary["scenarios"]
    target_total = min(TARGET_HITS, total)
    passed = summary["hits"] >= target_total and summary["evidence_ok"]
    lines += [
        "",
        f"- 1순위 적중: **{summary['hits']}/{total}** (목표 {target_total}/{total} 이상)",
        f"- 모든 원인 후보에 증거 링크: **{'예' if summary['evidence_ok'] else '아니오'}**",
        f"- 평균 도구 호출: {summary['avg_tool_calls']}회",
        f"- 평균 응답 시간: {summary['avg_duration_s']}초",
        f"- 질문당 평균 비용: ${summary['avg_cost_usd']:.4f} (총 ${summary['total_cost_usd']:.4f})",
        f"- 목표 달성: **{'✅' if passed else '❌'}**",
    ]
    return "\n".join(lines) + "\n"


def target_met(scores: list[ScenarioScore]) -> bool:
    summary = summarize(scores)
    return summary["hits"] >= min(TARGET_HITS, summary["scenarios"]) and summary["evidence_ok"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KubeLens 평가 하네스")
    parser.add_argument("--scenarios", default="", help="쉼표로 구분 (예: S1,S3). 기본: 전체")
    parser.add_argument("--model", default=None)
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    import os

    from kubelens.agent import diagnose
    from kubelens.agent.llm import DEFAULT_MODEL, anthropic_llms
    from kubelens.tools import ReadOnlyKube

    model = args.model or os.environ.get("KUBELENS_MODEL", DEFAULT_MODEL)
    scenarios = load_scenarios(only=[s for s in args.scenarios.split(",") if s.strip()])
    kube = ReadOnlyKube.from_kubeconfig()
    llms = anthropic_llms(model)
    run_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    scores = run(scenarios, lambda q, ns: diagnose(q, kube, llms, namespace=ns), model)
    markdown = render_markdown(scores, model, run_at)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "latest.md").write_text(markdown)
    (args.output_dir / "latest.json").write_text(
        json.dumps(
            {
                "run_at": run_at,
                "model": model,
                "summary": summarize(scores),
                "scenarios": [asdict(s) for s in scores],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(markdown)
    return 0 if target_met(scores) else 1


if __name__ == "__main__":
    sys.exit(main())
