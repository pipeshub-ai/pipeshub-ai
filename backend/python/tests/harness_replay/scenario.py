"""A scenario: one user turn, the world it runs in, and the model's script.

Replay scenarios (``tests/harness_replay/scenarios/*.yaml``) and parity cases
(``tests/support/parity_cases/harness/**.yaml``) share this shape; a parity case
adds graders and safety checks on top.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from harness_core.testing import (
    ModelScript,  # noqa: TC001 - pydantic resolves it at runtime
)
from tests.harness_replay.legacy_driver import DriveSpec
from tests.harness_replay.world import TOOLSETS, WorldFixtures

SCENARIO_DIR = Path(__file__).parent / "scenarios"

ChatMode = Literal["quick", "react", "planExecute", "deep", "internal_search"]


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str = ""
    route: Literal["chat", "agent"] = "agent"
    chat_mode: ChatMode = "quick"
    task: str
    fixtures: WorldFixtures = WorldFixtures()
    script: ModelScript | None = None
    env: dict[str, str] = {}
    # ``fixtures.knowledge`` as the agent's knowledge base (the product's knowledgegraph search and its
    # "Knowledge sources" prompt section) beside the other connectors; not as the fake ``knowledge``
    # connector, which the product prompt truthfully calls no knowledge source (P4.9 IN-1). P4.9 dropped
    # the connectors only because of FB-7, which P4.10 fixed.
    knowledge_base: bool = False

    def drive_spec(self) -> DriveSpec:
        script = self.script.model_copy(update={"id": self.id}) if self.script else None
        return DriveSpec(
            scenario=self.id,
            query=self.task,
            route=self.route,
            chat_mode=self.chat_mode,
            script=script,
            fixtures=self.fixtures,
            env=dict(self.env),
            knowledge_search=self.knowledge_base,
            fake_toolsets=tuple(t for t in TOOLSETS if t != "knowledge") if self.knowledge_base else None,
        )


def load_scenario(path: Path) -> Scenario:
    return Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_scenarios(directory: Path = SCENARIO_DIR) -> list[Scenario]:
    return [load_scenario(p) for p in sorted(directory.glob("*.yaml"))]


__all__ = ["SCENARIO_DIR", "ChatMode", "Scenario", "load_scenario", "load_scenarios"]
