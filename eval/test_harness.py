"""평가 하네스 채점 로직 테스트 (LLM·클러스터 불필요)."""

from __future__ import annotations

import harness

from kubelens.agent import AgentError, Diagnosis, DiagnosisResult, Evidence, RootCause


def result(
    title: str, explanation: str, refs: list[str] | None = None, output: str = ""
) -> DiagnosisResult:
    refs = ["tc_1"] if refs is None else refs
    diag = Diagnosis(
        summary=title,
        root_causes=[
            RootCause(title=title, confidence="high", explanation=explanation, evidence_refs=refs)
        ],
        next_actions=[],
    )
    ev = [Evidence("tc_1", "get_pod_logs", {}, output, False, 5, "ok")]
    return DiagnosisResult(
        question="q",
        diagnosis=diag,
        evidence=ev,
        valid=bool(refs),
        usage={"input_tokens": 10_000, "output_tokens": 1_000, "llm_calls": 4},
    )


def scenario(sid: str) -> dict:
    return next(s for s in harness.load_scenarios()["scenarios"] if s["id"] == sid)


def test_expected_yaml_has_six_scenarios() -> None:
    data = harness.load_scenarios()
    assert [s["id"] for s in data["scenarios"]] == ["S1", "S2", "S3", "S4", "S5", "S6"]
    assert data["namespace"] == "kubelens-chaos"
    assert [s["id"] for s in harness.load_scenarios(only=["s3"])["scenarios"]] == ["S3"]


def test_hit_requires_all_groups_case_insensitive() -> None:
    s = harness.score(
        scenario("S1"), result("필수 환경변수 db_host 누락", "exit code 1"), "claude-sonnet-5-5"
    )
    assert s.hit and not s.missing_groups
    miss = harness.score(
        scenario("S1"), result("DB 연결 실패", "DB_HOST 확인 필요"), "claude-sonnet-5-5"
    )
    assert not miss.hit
    assert miss.missing_groups == [["환경변수", "환경 변수", "environment variable", "env"]]


def test_hit_requires_valid_evidence() -> None:
    s = harness.score(
        scenario("S1"), result("환경변수 DB_HOST 누락", "", refs=[]), "claude-sonnet-5-5"
    )
    assert not s.hit and not s.valid


def test_leak_fails_scenario() -> None:
    leaky = result("환경변수 DB_HOST 누락", "", output="api_key=kl-fake-0000000000000000")
    s = harness.score(scenario("S1"), leaky, "claude-sonnet-5-5")
    assert not s.hit
    assert s.leaked == ["kl-fake-0000000000000000"]


def test_cost_uses_model_prices() -> None:
    s = harness.score(
        scenario("S4"), result("Insufficient memory", "64Gi 요청"), "claude-sonnet-5-5"
    )
    assert s.hit
    assert abs(s.cost_usd - (10_000 * 2 + 1_000 * 10) / 1_000_000) < 1e-9


def test_run_counts_agent_errors_as_miss_and_renders_table() -> None:
    data = harness.load_scenarios(only=["S1", "S2"])

    def fake_diagnose(question: str, namespace: str | None) -> DiagnosisResult:
        assert namespace == "kubelens-chaos"
        if "payment-api" in question:
            return result("환경변수 DB_HOST 누락", "exit code 1")
        raise AgentError("refusal")

    scores = harness.run(data, fake_diagnose, "claude-sonnet-5-5", log=lambda _m: None)
    assert [s.hit for s in scores] == [True, False]
    assert scores[1].error == "refusal"
    md = harness.render_markdown(scores, "claude-sonnet-5-5", "now")
    assert "| S1 | ✅ | ✅ |" in md
    assert "오류: refusal" in md
    assert "1순위 적중: **1/2**" in md
    assert not harness.target_met(scores)
