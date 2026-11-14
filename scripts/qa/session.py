"""A managed stack that outlives one `qa run`: `qa stack up` starts it, `qa run --session` runs
tests against it as many times as needed, and `qa stack down` removes it. The QA agent uses it so
each round of tests does not build and seed a new stack."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import run as run_mod
from . import stack as stack_mod
from .config import Config

FILE = "session.json"


def path(out: Path) -> Path:
    return out / "stack" / FILE


def up(cfg: Config, out: Path, plan_stack: dict[str, Any] | None = None, graph_db: str = "neo4j",
       build: bool = True, head: str | None = None) -> dict[str, Any]:
    """Build, start and seed one managed stack with the broker and compose profiles of plan_stack
    (a plan's "stack" block); save what `qa run --session` needs to reach it."""
    given = plan_stack or {}
    plan = {"head": head, "stack": {"needed": True, "graph_db": [graph_db], "broker": given.get("broker", "redis"),
                                    "compose_profiles": list(given.get("compose_profiles", []))}}
    stacks = run_mod._stacks(cfg, plan, "managed", None, out / "logs", build, out)
    error = run_mod._prepare(cfg, stacks, out)
    if error:
        raise RuntimeError(error)
    stack = stacks[0]
    os.environ.update(stack_mod.run_credentials(os.environ))
    # The stack serves every round, so the fixtures leave the models they seeded for the next one.
    os.environ.setdefault(run_mod.LEAVE_MODELS, "1")
    before = dict(os.environ)
    try:
        stack.start()
    except Exception:
        stack.collect_logs()
        stack.stop()
        raise
    env = {k: v for k, v in stack.env().items() if before.get(k) != v or k.startswith(("PIPESHUB_TEST_USER_", "PIPESHUB_TEST_LEAVE"))}
    env.update({"PIPESHUB_BASE_URL": stack.base_url, "QA_STACK_GRAPH_DB": graph_db, "TEST_GRAPH_DB_TYPE": graph_db})
    data = {
        "schema": "qa-session/v1", "project": stack.project, "graph_db": graph_db, "broker": stack.broker,
        "compose_profiles": stack.compose_profiles, "base_url": stack.base_url, "overrides": stack.overrides,
        "extra_env": stack.extra_env, "image": stack_mod.image_tag(head) if build else "", "env": env,
    }
    p = path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Holds the stack's admin password: owner-only, and never part of an uploaded artifact.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    return data


def load(file: Path) -> dict[str, Any]:
    data = json.loads(Path(file).read_text(encoding="utf-8"))
    if data.get("schema") != "qa-session/v1":
        raise ValueError(f"{file} is not a qa stack session")
    return data


def use(file: Path) -> str:
    """Point this process at a saved session; returns its base URL for `run.run(base_url=...)`."""
    data = load(file)
    os.environ.update(data["env"])
    return data["base_url"]


def down(cfg: Config, out: Path) -> list[str]:
    """Collect the logs and remove the stack (volumes too) and the image it built. Safe to call twice."""
    p = path(out)
    if not p.is_file():
        return ["no session"]
    data = load(p)
    stack = stack_mod.Stack(cfg.root, "managed", data["graph_db"], data["broker"], data["compose_profiles"],
                            data["project"], data["base_url"], out / "logs")
    stack.overrides = list(data["overrides"])
    stack.extra_env = dict(data["extra_env"])
    stack.up = True
    stack.collect_logs()
    stack.stop()
    if data.get("image") and os.environ.get("QA_KEEP_IMAGE") != "1":
        run_mod._remove_image(data["image"], stack.events)
    p.unlink(missing_ok=True)
    return stack.events
