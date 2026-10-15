"""KubeLens API (기획서 10장). 진단 질문은 SSE로 진행 상황을 스트리밍한다.

실행: make api  (기본 http://127.0.0.1:8000, frontend/dist가 있으면 UI도 함께 서빙)
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from kubernetes import config as kube_config
from pydantic import BaseModel
from sqlalchemy import Engine
from sqlmodel import Session, col, select

from kubelens.agent import DiagnosisCancelled, diagnose
from kubelens.api.db import Cluster, DiagnosisSession, Message, ToolCall, make_engine
from kubelens.tools import ReadOnlyKube
from kubelens.tools.kube import default_kubeconfig_path

KubeFactory = Callable[[str | None, str | None], ReadOnlyKube]
LLMFactory = Callable[[], Any]

DEFAULT_TITLE = "새 진단"
DEFAULT_DIAGNOSIS_TIMEOUT_S = 180.0
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _load(spec: str) -> Any:
    """'package.module:attr' 형식의 팩토리를 불러온다 (테스트·데모에서 가짜 구현 주입용)."""
    module, _, attr = spec.partition(":")
    return getattr(importlib.import_module(module), attr)


def _default_kube_factory() -> KubeFactory:
    spec = os.environ.get("KUBELENS_KUBE_FACTORY")
    return _load(spec) if spec else ReadOnlyKube.from_kubeconfig


def default_kubeconfig_dirs() -> list[Path]:
    """API로 받을 수 있는 kubeconfig 위치. KUBELENS_KUBECONFIG_DIRS(os.pathsep 구분)로 변경."""
    env = os.environ.get("KUBELENS_KUBECONFIG_DIRS")
    if env:
        return [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    return [default_kubeconfig_path().parent, Path.home() / ".kube"]


def _default_timeout() -> float | None:
    value = float(os.environ.get("KUBELENS_DIAGNOSIS_TIMEOUT", DEFAULT_DIAGNOSIS_TIMEOUT_S))
    return value if value > 0 else None


def _default_llm_factory() -> LLMFactory:
    spec = os.environ.get("KUBELENS_LLM_FACTORY", "kubelens.agent.llm:anthropic_llms")
    return _load(spec)


# --- 요청 바디 -------------------------------------------------------------


class ClusterCreate(BaseModel):
    name: str
    kubeconfig_path: str | None = None
    context: str | None = None


class SessionCreate(BaseModel):
    cluster_id: int
    title: str | None = None


class MessageCreate(BaseModel):
    content: str
    namespace: str | None = None


# --- 직렬화 ----------------------------------------------------------------


def _cluster_out(c: Cluster) -> dict[str, Any]:
    return c.model_dump(mode="json")


def _toolcall_out(t: ToolCall, with_output: bool) -> dict[str, Any]:
    data = t.model_dump(mode="json", exclude={"args_json", "output_text"})
    data["args"] = json.loads(t.args_json)
    if with_output:
        data["output_text"] = t.output_text
    return data


def _message_out(m: Message, tool_calls: list[ToolCall]) -> dict[str, Any]:
    data = m.model_dump(mode="json", exclude={"diagnosis_json"})
    data["result"] = json.loads(m.diagnosis_json) if m.diagnosis_json else None
    data["tool_calls"] = [_toolcall_out(t, with_output=False) for t in tool_calls]
    return data


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def create_app(
    engine: Engine | None = None,
    kube_factory: KubeFactory | None = None,
    llm_factory: LLMFactory | None = None,
    serve_frontend: bool = True,
    kubeconfig_dirs: list[Path] | None = None,
    diagnosis_timeout_s: float | None = -1.0,
) -> FastAPI:
    """diagnosis_timeout_s: 증거 수집 시간 상한. -1이면 환경변수/기본값(180초), None이면 무제한."""
    state: dict[str, Any] = {}
    allowed_dirs = [
        d.resolve()
        for d in (kubeconfig_dirs if kubeconfig_dirs is not None else default_kubeconfig_dirs())
    ]
    timeout_s = _default_timeout() if diagnosis_timeout_s == -1.0 else diagnosis_timeout_s
    llm_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        state["engine"] = engine or make_engine()
        state["kube_factory"] = kube_factory or _default_kube_factory()
        state["llm_factory"] = llm_factory or _default_llm_factory()
        yield

    app = FastAPI(title="KubeLens", version="0.1.0", lifespan=lifespan)

    def db() -> Session:
        return Session(state["engine"])

    def llms() -> Any:
        with llm_lock:
            if "llms" not in state:
                state["llms"] = state["llm_factory"]()
            return state["llms"]

    def kube_for(kubeconfig_path: str, context: str | None) -> ReadOnlyKube:
        return state["kube_factory"](kubeconfig_path, context)

    def checked_kubeconfig(path: str | None) -> str:
        """허용된 디렉터리 안의 경로만 받는다 (임의 파일을 읽어 보는 용도로 쓰이지 않게)."""
        resolved = Path(path or default_kubeconfig_path()).expanduser().resolve()
        if not any(resolved.is_relative_to(d) for d in allowed_dirs):
            raise HTTPException(
                400,
                "허용되지 않은 kubeconfig 경로입니다. 허용 위치: "
                + ", ".join(str(d) for d in allowed_dirs)
                + " (KUBELENS_KUBECONFIG_DIRS로 변경)",
            )
        return str(resolved)

    # --- 클러스터 ----------------------------------------------------------

    @app.get("/api/kube/contexts")
    def kube_contexts(kubeconfig_path: str | None = None) -> dict[str, Any]:
        path = checked_kubeconfig(kubeconfig_path)
        try:
            contexts, active = kube_config.list_kube_config_contexts(config_file=path)
        except Exception as exc:
            # 파싱 오류 메시지에는 파일 내용(토큰 등)이 섞일 수 있으므로 예외 종류만 알린다
            raise HTTPException(
                400, f"kubeconfig를 읽을 수 없습니다: {path} ({type(exc).__name__})"
            ) from exc
        return {
            "kubeconfig_path": path,
            "contexts": [
                {
                    "name": c["name"],
                    "cluster": c["context"].get("cluster"),
                    "current": bool(active) and c["name"] == active["name"],
                }
                for c in contexts
            ],
        }

    @app.get("/api/clusters")
    def list_clusters() -> list[dict[str, Any]]:
        with db() as s:
            rows = s.exec(select(Cluster).order_by(col(Cluster.id).desc())).all()
            return [_cluster_out(c) for c in rows]

    @app.post("/api/clusters", status_code=201)
    def create_cluster(body: ClusterCreate) -> dict[str, Any]:
        cluster = Cluster(
            name=body.name,
            kubeconfig_path=checked_kubeconfig(body.kubeconfig_path),
            context=body.context,
        )
        with db() as s:
            s.add(cluster)
            s.commit()
            s.refresh(cluster)
            return _cluster_out(cluster)

    @app.post("/api/clusters/{cluster_id}/test")
    def test_cluster(cluster_id: int) -> dict[str, Any]:
        with db() as s:
            cluster = s.get(Cluster, cluster_id)
            if cluster is None:
                raise HTTPException(404, "cluster not found")
            try:
                kube = kube_for(cluster.kubeconfig_path, cluster.context)
                namespaces = len(kube.list_namespace().items or [])
                version = kube.read_version()
            except Exception as exc:
                return {"ok": False, "message": f"연결 실패: {type(exc).__name__}: {exc}"}
            cluster.server_version = version
            s.add(cluster)
            s.commit()
            return {
                "ok": True,
                "message": f"연결 성공 (네임스페이스 {namespaces}개)",
                "server_version": version,
            }

    # --- 세션 --------------------------------------------------------------

    @app.get("/api/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        with db() as s:
            rows = s.exec(
                select(DiagnosisSession).order_by(col(DiagnosisSession.created_at).desc())
            ).all()
            return [r.model_dump(mode="json") for r in rows]

    @app.post("/api/sessions", status_code=201)
    def create_session(body: SessionCreate) -> dict[str, Any]:
        with db() as s:
            if s.get(Cluster, body.cluster_id) is None:
                raise HTTPException(404, "cluster not found")
            session = DiagnosisSession(
                cluster_id=body.cluster_id, title=body.title or DEFAULT_TITLE
            )
            s.add(session)
            s.commit()
            s.refresh(session)
            return session.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: int) -> dict[str, Any]:
        with db() as s:
            session = s.get(DiagnosisSession, session_id)
            if session is None:
                raise HTTPException(404, "session not found")
            messages = s.exec(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(col(Message.created_at), col(Message.id))
            ).all()
            out = session.model_dump(mode="json")
            out["messages"] = []
            for m in messages:
                calls = s.exec(
                    select(ToolCall)
                    .where(ToolCall.message_id == m.id)
                    .order_by(col(ToolCall.created_at))
                ).all()
                out["messages"].append(_message_out(m, list(calls)))
            return out

    @app.get("/api/toolcalls/{toolcall_id}")
    def get_toolcall(toolcall_id: str) -> dict[str, Any]:
        with db() as s:
            t = s.get(ToolCall, toolcall_id)
            if t is None:
                raise HTTPException(404, "toolcall not found")
            return _toolcall_out(t, with_output=True)

    # --- 진단 질문 (SSE) ---------------------------------------------------

    @app.post("/api/sessions/{session_id}/messages")
    async def post_message(session_id: int, body: MessageCreate) -> StreamingResponse:
        with db() as s:
            session = s.get(DiagnosisSession, session_id)
            if session is None:
                raise HTTPException(404, "session not found")
            cluster = s.get(Cluster, session.cluster_id)
            assert cluster is not None
            kubeconfig_path, context = cluster.kubeconfig_path, cluster.context
            if session.title == DEFAULT_TITLE:
                session.title = body.content[:60]
                s.add(session)
            s.add(Message(session_id=session_id, role="user", content=body.content))
            assistant = Message(session_id=session_id, role="assistant", content="")
            s.add(assistant)
            s.commit()
            s.refresh(assistant)
            assistant_id = assistant.id
        assert assistant_id is not None

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        cancel = threading.Event()  # 클라이언트가 연결을 끊으면 진단을 멈춘다

        def push(event: str, data: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (event, data))

        def work() -> None:
            refs: dict[str, str] = {}  # 에이전트 evidence id(tc_N) → ToolCall.id

            def on_event(event: str, data: dict[str, Any]) -> None:
                if event == "tool_start":
                    refs[data["id"]] = uuid.uuid4().hex[:12]
                    push("tool_start", {**data, "id": refs[data["id"]], "ref": data["id"]})
                elif event == "tool_end":
                    tc_id = refs[data["id"]]
                    with db() as s:
                        s.add(
                            ToolCall(
                                id=tc_id,
                                message_id=assistant_id,
                                tool_name=data["tool_name"],
                                args_json=json.dumps(data["args"], ensure_ascii=False),
                                output_text=data["output"],
                                truncated=data["truncated"],
                                duration_ms=data["duration_ms"],
                                status=data["status"],
                            )
                        )
                        s.commit()
                    public = {k: v for k, v in data.items() if k not in ("output", "args")}
                    push("tool_end", {**public, "id": tc_id, "ref": data["id"]})
                elif event == "thinking":
                    push("thinking", data)
                # final/error는 아래에서 DB id로 바꿔 직접 보낸다

            try:
                kube = kube_for(kubeconfig_path, context)
                result = diagnose(
                    body.content,
                    kube,
                    llms(),
                    body.namespace,
                    on_event,
                    timeout_s=timeout_s,
                    should_stop=cancel.is_set,
                )
                diagnosis = result.diagnosis.model_dump() if result.diagnosis else None
                if diagnosis:
                    for cause in diagnosis["root_causes"]:
                        cause["evidence_refs"] = [refs.get(r, r) for r in cause["evidence_refs"]]
                payload = {
                    "diagnosis": diagnosis,
                    "valid": result.valid,
                    "validation_errors": result.validation_errors,
                    "usage": result.usage,
                    "timed_out": result.timed_out,
                }
                with db() as s:
                    msg = s.get(Message, assistant_id)
                    assert msg is not None
                    msg.content = diagnosis["summary"] if diagnosis else ""
                    msg.diagnosis_json = json.dumps(payload, ensure_ascii=False)
                    s.add(msg)
                    s.commit()
                push("final", {"message_id": assistant_id, **payload})
            except DiagnosisCancelled:
                with db() as s:
                    msg = s.get(Message, assistant_id)
                    if msg is not None:
                        msg.content = "진단 취소됨 (클라이언트 연결 끊김)"
                        s.add(msg)
                        s.commit()
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                with db() as s:
                    msg = s.get(Message, assistant_id)
                    if msg is not None:
                        msg.content = f"진단 실패: {message}"
                        s.add(msg)
                        s.commit()
                push("error", {"message_id": assistant_id, "message": message})
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        async def stream() -> AsyncIterator[str]:
            worker = asyncio.create_task(asyncio.to_thread(work))
            try:
                while (item := await queue.get()) is not None:
                    yield _sse(*item)
                await worker
            finally:
                if not worker.done():  # 응답 도중 연결이 끊겼다
                    cancel.set()

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if serve_frontend and FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


app = create_app()
