"""실제 uvicorn 서버를 띄워 동시 질문과 연결 끊김(취소)을 검증한다."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from langchain_core.messages import AIMessage, AnyMessage

from kubelens.api.app import create_app
from kubelens.api.db import make_engine
from tests.fake_llms import ScriptedLLMs
from tests.fakes import fake_kube


class SlowLLMs(ScriptedLLMs):
    """LLM 턴마다 delay초가 걸리는 대본 LLM (동시 실행이 실제로 겹치게)."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        inner = self.actor
        planner = self.planner

        class Slow:
            def __init__(self, target: Any) -> None:
                self.target = target

            def invoke(self, messages: list[AnyMessage]) -> AIMessage:
                time.sleep(delay)
                return self.target.invoke(messages)

        self.actor = Slow(inner)
        self.planner = Slow(planner)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _serve(tmp_path: Path, delay: float) -> Iterator[str]:
    app = create_app(
        engine=make_engine(f"sqlite:///{tmp_path / 'c.db'}"),
        kube_factory=fake_kube,
        llm_factory=lambda: SlowLLMs(delay),
        serve_frontend=False,
    )
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "서버 기동 실패"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


@pytest.fixture
def fast_server(tmp_path: Path) -> Iterator[str]:
    yield from _serve(tmp_path, delay=0.05)


@pytest.fixture
def slow_server(tmp_path: Path) -> Iterator[str]:
    yield from _serve(tmp_path, delay=0.5)


def _new_session(c: httpx.Client) -> int:
    cluster = c.post("/api/clusters", json={"name": "fake"}).json()
    return int(c.post("/api/sessions", json={"cluster_id": cluster["id"]}).json()["id"])


def _ask(base: str, question: str) -> tuple[int, list[tuple[str, dict[str, Any]]]]:
    with httpx.Client(base_url=base, timeout=60) as c:
        sid = _new_session(c)
        res = c.post(f"/api/sessions/{sid}/messages", json={"content": question})
        events = []
        for block in res.text.strip().split("\n\n"):
            lines = dict(line.split(": ", 1) for line in block.splitlines())
            events.append((lines["event"], json.loads(lines["data"])))
        return sid, events


def test_concurrent_questions_are_isolated(fast_server: str) -> None:
    n = 8
    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(lambda i: _ask(fast_server, f"질문 {i}"), range(n)))

    all_tool_ids: list[str] = []
    for sid, events in results:
        assert events[-1][0] == "final", events[-1]
        tool_ids = [d["id"] for t, d in events if t == "tool_end"]
        assert len(tool_ids) == 2
        all_tool_ids += tool_ids
        refs = events[-1][1]["diagnosis"]["root_causes"][0]["evidence_refs"]
        assert set(refs) <= set(tool_ids)  # 다른 질문의 증거를 가리키지 않는다
        session = httpx.get(f"{fast_server}/api/sessions/{sid}").json()
        assert [m["role"] for m in session["messages"]] == ["user", "assistant"]
        assert [t["id"] for t in session["messages"][1]["tool_calls"]] == tool_ids
    assert len(set(all_tool_ids)) == n * 2


def test_client_disconnect_cancels_diagnosis(slow_server: str) -> None:
    with httpx.Client(base_url=slow_server, timeout=30) as c:
        sid = _new_session(c)
        with c.stream("POST", f"/api/sessions/{sid}/messages", json={"content": "끊김"}) as res:
            for line in res.iter_lines():
                if line == "event: tool_start":
                    break  # 첫 도구가 시작되자마자 연결을 끊는다

        deadline = time.monotonic() + 10
        while True:
            assistant = c.get(f"/api/sessions/{sid}").json()["messages"][1]
            if assistant["content"] or time.monotonic() > deadline:
                break
            time.sleep(0.2)

    assert assistant["content"] == "진단 취소됨 (클라이언트 연결 끊김)"
    assert assistant["result"] is None
    assert len(assistant["tool_calls"]) <= 1  # 남은 도구 호출과 보고서 작성은 하지 않았다
