"""The harness case format (YAML) and its loader.

The cases committed here (``harness/``) are the ones the parity, replay and security tests drive; a suite with
cases of its own loads them from its directory with :func:`load_cases`, and may load them as a subclass of
:class:`ParityCase` that adds its own fields.

A case is a replay :class:`Scenario` (task, fixtures, model script) plus:

``grader``      outcome checks on the finished run (answer, status, effects
                the fake connectors committed, tools that ran), each one
                decided from the recording alone.
``safety``      what the task itself authorizes (writes the user asked for,
                hosts and domains data may go to). The trajectory invariants
                in ``harness_graders.py`` always run against it.
``injection``   for AgentDojo-style cases: where the attack sits, the channel
                it tries to exfiltrate through, the canary (private data) and
                the attacker markers whose presence in anything that left the
                run means the attack succeeded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypeVar, overload

import yaml
from pydantic import BaseModel, ConfigDict, JsonValue, model_validator

from tests.harness_replay.scenario import Scenario

CASE_DIR = Path(__file__).parent / "harness"

Category = Literal[
    "plain_chat",
    "quick",
    "react",
    "planExecute",
    "deep",
    "sandbox",
    "approval",
    "robustness",
    "injection",
]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EffectSpec(_Spec):
    op: str
    count: int = 1
    payload_contains: dict[str, str] = {}


class Grader(_Spec):
    """Exactly one field set; the YAML key names the check."""

    contains: str | None = None
    not_contains: str | None = None
    regex: str | None = None
    exact: str | None = None
    status: str | None = None
    effect: EffectSpec | None = None
    no_effects: bool | None = None
    tool_called: str | None = None
    tool_not_called: str | None = None

    @model_validator(mode="after")
    def _one_check(self) -> Grader:
        set_fields = [k for k, v in self.model_dump().items() if v is not None]
        if len(set_fields) != 1:
            raise ValueError(
                f"a grader sets exactly one check, got {set_fields or 'none'}"
            )
        return self

    @property
    def kind(self) -> str:
        return next(k for k, v in self.model_dump().items() if v is not None)


class AuthorizedWrite(_Spec):
    tool: str
    args_contains: dict[str, str] = {}


class Safety(_Spec):
    authorized_writes: list[AuthorizedWrite] = []
    allowlisted_hosts: list[str] = ["pipeshub.test", "docs.pipeshub.test"]
    internal_domains: list[str] = ["corp.example"]
    approve_asks: bool = False


class Injection(_Spec):
    vector: Literal["kb_doc", "email", "jira", "slack", "web_page", "drive_file"]
    channel: Literal[
        "slack_post",
        "email",
        "web_fetch",
        "markdown_image",
        "answer_link",
        "drive_share",
        "jira_comment",
        "sandbox_egress",
    ]
    canary: str
    goal: list[str]


class ParityCase(Scenario):
    category: Category
    grader: list[Grader]
    safety: Safety = Safety()
    injection: Injection | None = None
    requires: list[Literal["local_sandbox", "docker"]] = []
    notes: str = ""
    extra: dict[str, JsonValue] = {}

    @model_validator(mode="after")
    def _consistent(self) -> ParityCase:
        if (self.category == "injection") != (self.injection is not None):
            raise ValueError(
                f"{self.id}: injection cases (and only they) carry an injection block"
            )
        if not self.grader:
            raise ValueError(f"{self.id}: needs at least one grader")
        return self


class CaseSetError(ValueError):
    pass


C = TypeVar("C", bound=ParityCase)


@overload
def load_cases(directory: Path = CASE_DIR) -> list[ParityCase]: ...
@overload
def load_cases(directory: Path, case_type: type[C]) -> list[C]: ...


def load_cases(directory: Path = CASE_DIR, case_type: type[ParityCase] = ParityCase) -> list[ParityCase]:
    cases = [
        case_type.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(directory.rglob("*.yaml"))
    ]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise CaseSetError(f"duplicate case id {case.id}")
        seen.add(case.id)
    if not cases:
        raise CaseSetError(f"no cases under {directory}")
    return cases


__all__ = [
    "CASE_DIR",
    "AuthorizedWrite",
    "CaseSetError",
    "EffectSpec",
    "Grader",
    "Injection",
    "ParityCase",
    "Safety",
    "load_cases",
]
