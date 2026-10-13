"""API 테스트: 가짜 클러스터 + 가짜 LLM으로 엔드포인트와 SSE 이벤트 순서를 검증한다."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from kubelens.api.app import create_app
from kubelens.api.db import make_engine
from tests.fake_llms import scripted_llms
from tests.fakes import fake_kube


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(
        engine=make_engine(f"sqlite:///{tmp_path / 'test.db'}"),
        kube_factory=fake_kube,
        llm_factory=scripted_llms,
        serve_frontend=False,
    )
    with TestClient(app) as c:
        yield c


def parse_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def new_session(client: TestClient) -> int:
    cluster = client.post("/api/clusters", json={"name": "kind", "context": "kind-kubelens"}).json()
    return client.post("/api/sessions", json={"cluster_id": cluster["id"]}).json()["id"]


def test_kube_contexts_lists_names_only(client: TestClient, tmp_path: Path) -> None:
    kubeconfig = tmp_path / "config"
    kubeconfig.write_text(
        """
apiVersion: v1
kind: Config
clusters: [{name: c1, cluster: {server: "https://127.0.0.1:6443"}}]
users: [{name: u1, user: {token: super-secret-token}}]
contexts:
  - {name: kind-kubelens, context: {cluster: c1, user: u1}}
  - {name: other, context: {cluster: c1, user: u1}}
current-context: kind-kubelens
"""
    )
    res = client.get("/api/kube/contexts", params={"kubeconfig_path": str(kubeconfig)})
    assert res.status_code == 200
    body = res.json()
    assert [c["name"] for c in body["contexts"]] == ["kind-kubelens", "other"]
    assert body["contexts"][0]["current"] is True
    assert "super-secret-token" not in res.text


def test_kube_contexts_missing_file(client: TestClient) -> None:
    res = client.get("/api/kube/contexts", params={"kubeconfig_path": "/nope/config"})
    assert res.status_code == 400


def test_cluster_register_and_connection_test(client: TestClient) -> None:
    created = client.post("/api/clusters", json={"name": "kind", "context": "kind-kubelens"})
    assert created.status_code == 201
    cid = created.json()["id"]
    result = client.post(f"/api/clusters/{cid}/test").json()
    assert result["ok"] is True
    assert result["server_version"] == "v1.33.1"
    assert "네임스페이스 3개" in result["message"]
    assert client.get("/api/clusters").json()[0]["server_version"] == "v1.33.1"
    assert client.post("/api/clusters/999/test").status_code == 404


def test_cluster_connection_failure_is_reported(tmp_path: Path) -> None:
    def broken(_path: str | None, _ctx: str | None) -> Any:
        raise ConnectionError("refused")

    app = create_app(
        engine=make_engine(f"sqlite:///{tmp_path / 'b.db'}"),
        kube_factory=broken,
        llm_factory=scripted_llms,
        serve_frontend=False,
    )
    with TestClient(app) as c:
        cid = c.post("/api/clusters", json={"name": "x"}).json()["id"]
        result = c.post(f"/api/clusters/{cid}/test").json()
    assert result == {"ok": False, "message": "연결 실패: ConnectionError: refused"}


def test_message_streams_sse_in_order_and_persists(client: TestClient) -> None:
    sid = new_session(client)
    res = client.post(f"/api/sessions/{sid}/messages", json={"content": "문제 있는 파드 있어?"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(res.text)
    types = [t for t, _ in events]

    assert types[0] == "thinking"
    assert types[-1] == "final"
    assert types.count("tool_start") == 2 and types.count("tool_end") == 2
    # 각 도구는 start 다음에 end가 오고, 모두 final 이전에 온다
    starts = [i for i, t in enumerate(types) if t == "tool_start"]
    ends = [i for i, t in enumerate(types) if t == "tool_end"]
    assert all(s < e for s, e in zip(starts, ends, strict=True))
    assert max(ends) < types.index("final")
    # SSE에는 도구 출력 원문을 싣지 않는다
    assert all("output" not in d for t, d in events if t == "tool_end")

    final = events[-1][1]
    assert final["valid"] is True
    tool_ids = [d["id"] for t, d in events if t == "tool_end"]
    refs = final["diagnosis"]["root_causes"][0]["evidence_refs"]
    assert refs and set(refs) <= set(tool_ids)  # evidence_refs = ToolCall ID

    # 증거 원본 조회 (마스킹된 출력)
    evidence = client.get(f"/api/toolcalls/{refs[0]}").json()
    assert evidence["tool_name"] == "list_pods"
    assert "payment-api" in evidence["output_text"]
    assert evidence["args"] == {"namespace": "*", "only_unhealthy": True}

    # 새로고침 후에도 유지 (세션 히스토리)
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["title"] == "문제 있는 파드 있어?"
    roles = [m["role"] for m in session["messages"]]
    assert roles == ["user", "assistant"]
    assistant = session["messages"][1]
    assert assistant["result"]["diagnosis"]["root_causes"][0]["evidence_refs"] == refs
    assert [t["id"] for t in assistant["tool_calls"]] == tool_ids
    assert "output_text" not in assistant["tool_calls"][0]
    assert client.get("/api/sessions").json()[0]["id"] == sid


def test_logs_are_masked_in_stored_evidence(client: TestClient, tmp_path: Path) -> None:
    from tests.fake_llms import ScriptedActor, tool_call

    llm = scripted_llms()
    pod = "payment-api-7d9f-abcde"
    llm.actor = ScriptedActor(
        [[tool_call("get_pod_logs", {"namespace": "kubelens-chaos", "name": pod}, "x")]]
    )
    app = create_app(
        engine=make_engine(f"sqlite:///{tmp_path / 'm.db'}"),
        kube_factory=fake_kube,
        llm_factory=lambda: llm,
        serve_frontend=False,
    )
    with TestClient(app) as c:
        sid = new_session(c)
        events = parse_sse(c.post(f"/api/sessions/{sid}/messages", json={"content": "로그"}).text)
        tc_id = next(d["id"] for t, d in events if t == "tool_end")
        out = c.get(f"/api/toolcalls/{tc_id}").json()["output_text"]
    assert "api_key=***" in out
    assert "kl-fake-0000000000000000" not in out


def test_agent_failure_emits_error_event(tmp_path: Path) -> None:
    from tests.fake_llms import FakeLLMs

    class Refuser:
        def invoke(self, messages: Any) -> Any:
            from langchain_core.messages import AIMessage

            return AIMessage(content="", response_metadata={"stop_reason": "refusal"})

    app = create_app(
        engine=make_engine(f"sqlite:///{tmp_path / 'e.db'}"),
        kube_factory=fake_kube,
        llm_factory=lambda: FakeLLMs(Refuser(), Refuser(), Refuser()),
        serve_frontend=False,
    )
    with TestClient(app) as c:
        sid = new_session(c)
        events = parse_sse(c.post(f"/api/sessions/{sid}/messages", json={"content": "?"}).text)
        assert events[-1][0] == "error"
        assert "거부" in events[-1][1]["message"]
        assistant = c.get(f"/api/sessions/{sid}").json()["messages"][1]
        assert assistant["content"].startswith("진단 실패")


def test_not_found(client: TestClient) -> None:
    assert client.get("/api/sessions/1").status_code == 404
    assert client.get("/api/toolcalls/nope").status_code == 404
    assert client.post("/api/sessions", json={"cluster_id": 42}).status_code == 404
    assert client.post("/api/sessions/1/messages", json={"content": "x"}).status_code == 404
