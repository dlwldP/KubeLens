"""진단 출력 스키마 (기획서 8장 출력 스키마)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field


class RootCause(BaseModel):
    title: str = Field(description="원인 한 줄 요약")
    confidence: Literal["high", "medium", "low"]
    explanation: str = Field(description="증거를 근거로 한 설명")
    evidence_refs: list[str] = Field(
        description="근거가 된 도구 호출 evidence_id 목록 (예: ['tc_2', 'tc_3']). 최소 1개."
    )


class NextAction(BaseModel):
    description: str
    command: str | None = Field(
        default=None, description="제안 명령어 (예: kubectl ...). KubeLens는 실행하지 않는다."
    )


class Diagnosis(BaseModel):
    summary: str
    root_causes: list[RootCause] = Field(description="가능성 높은 순서로 정렬한 원인 후보")
    next_actions: list[NextAction]


@dataclass(frozen=True)
class Evidence:
    """에이전트가 실행한 도구 호출 1건. id가 evidence_ref로 쓰인다."""

    id: str
    tool_name: str
    args: dict[str, Any]
    output: str
    truncated: bool
    duration_ms: int
    status: str


@dataclass
class DiagnosisResult:
    question: str
    diagnosis: Diagnosis | None
    evidence: list[Evidence]
    valid: bool
    validation_errors: list[str] = field(default_factory=list)
    synth_attempts: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    timed_out: bool = False  # 시간 상한으로 증거 수집을 일찍 끝냈는지

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "valid": self.valid,
            "validation_errors": self.validation_errors,
            "synth_attempts": self.synth_attempts,
            "usage": self.usage,
            "timed_out": self.timed_out,
            "diagnosis": self.diagnosis.model_dump() if self.diagnosis else None,
            "evidence": [
                {
                    "id": e.id,
                    "tool_name": e.tool_name,
                    "args": e.args,
                    "status": e.status,
                    "truncated": e.truncated,
                    "duration_ms": e.duration_ms,
                }
                for e in self.evidence
            ],
        }
