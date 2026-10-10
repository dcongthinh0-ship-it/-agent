from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field

from chemo_agent_product.core.domain import Contract, Reference


class AgentRequest(Contract):
    kind: Literal["RECOMMENDATION", "REVIEWER"]
    revision_id: UUID | None = None
    question: str = Field(default="", max_length=2000)


class Statement(Contract):
    message: str = Field(min_length=1, max_length=3000)
    candidate_id: UUID | None = None
    source_refs: list[Reference] = Field(min_length=1, max_length=30)


class ReviewFinding(Statement):
    code: str = Field(min_length=1, max_length=100)
    severity: Literal["INFO", "ATTENTION", "HIGH"]
    field_path: str | None = None


class FactProposal(Contract):
    fact_code: str = Field(min_length=1, max_length=100)
    value: str | float | bool | None
    status: Literal["ASSERTED", "NEGATED", "UNCERTAIN", "CONFLICT"]
    record_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2000)
    source_refs: list[Reference] = Field(min_length=1)


class AgentOutput(Contract):
    schema_version: Literal["agent-output.v1"] = "agent-output.v1"
    kind: Literal["RECOMMENDATION", "REVIEWER"]
    summary: str = Field(max_length=4000)
    summary_source_refs: list[Reference] = Field(default_factory=list, max_length=50)
    statements: list[Statement] = Field(default_factory=list, max_length=100)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    proposed_facts: list[FactProposal] = Field(default_factory=list, max_length=100)
    pending_questions: list[str] = Field(default_factory=list, max_length=100)
