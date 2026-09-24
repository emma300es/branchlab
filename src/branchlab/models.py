"""The model generates declarative probes, never executable test code."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Citation(StrictModel):
    path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    quote: str
    revision: Literal["base", "head"]


class Header(StrictModel):
    name: str
    value: str


class Step(StrictModel):
    id: str
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str
    headers: list[Header]
    body_json: str | None

    @model_validator(mode="after")
    def relative_path(self):
        if not self.path.startswith("/") or self.path.startswith("//") or "://" in self.path:
            raise ValueError("probe paths must be local HTTP paths")
        return self


class Assertion(StrictModel):
    step_id: str
    target: Literal["status", "json"]
    path: str
    operator: Literal["eq", "ne"]
    expected_json: str


class Probe(StrictModel):
    id: str
    title: str
    invariant: str
    intent: Literal["preserve", "change", "unknown"]
    severity: Literal["high", "medium", "low"]
    rationale: str
    citations: list[Citation]
    steps: list[Step] = Field(min_length=1, max_length=12)
    assertions: list[Assertion] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def valid_probe(self):
        import json
        import re
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", self.id):
            raise ValueError("probe id must be a safe identifier")
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate step ids")
        for step in self.steps:
            if step.body_json is not None:
                json.loads(step.body_json)
        for assertion in self.assertions:
            if assertion.step_id not in ids:
                raise ValueError("assertion references an absent step")
            json.loads(assertion.expected_json)
        return self


class Plan(StrictModel):
    intent_summary: str
    probes: list[Probe] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def unique_probes(self):
        if len({p.id for p in self.probes}) != len(self.probes):
            raise ValueError("duplicate probe ids")
        return self
