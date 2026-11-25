"""DEV-ONLY: which coding engine the durable lane's engine agent runs on (``E2E_ENGINE``, run.sh durable).

    python3 engines.py image <kind>      the image every run of that engine uses (exit 1: not a release engine)
    python3 engines.py kinds             every release engine, the default first (QAFINAL-FU-ENG: the docker lane runs
                                         the engine job specs on each)

Read from the release allowlist (backend/python/harness_engines/allowlist.json), the file the worker and the broker
read, so the lane asks for the image a run really starts and a new engine version needs no edit here. Standard
library only: run.sh and seed.py run it on the host.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

ALLOWLIST = Path(__file__).resolve().parents[2] / "backend" / "python" / "harness_engines" / "allowlist.json"
ORG_SETUP_ENV = "E2E_ORG_SETUP"
# Where a build keeps its own org setup when ``E2E_ORG_SETUP`` names none: ``<dir>/org_setup.py`` under here.
ORG_SETUP_ROOT = Path(__file__).resolve().parent


def _entries() -> list[dict[str, Any]]:
    return json.loads(ALLOWLIST.read_text())["engines"]


def default_kind() -> str:
    """The release default: the allowlist's first engine, the one an agent with ``engineProfile {}`` runs on."""
    return str(_entries()[0]["kind"])


def kinds() -> list[str]:
    """Every release engine once, in the allowlist's order (the default first)."""
    return list(dict.fromkeys(str(e["kind"]) for e in _entries()))


def image(kind: str) -> str:
    """The image of the kind's first entry (the version new conversations start on)."""
    for entry in _entries():
        if entry["kind"] == kind:
            return str(entry["image"])
    kinds = sorted({str(e["kind"]) for e in _entries()})
    raise KeyError(f"E2E_ENGINE={kind} is not a release engine ({', '.join(kinds)})")


def profile(kind: str | None) -> dict[str, Any]:
    """The engine agent's ``engineProfile``: ``{}`` for the release default, so the default lane stores what it always
    did; another kind narrows the agent to that engine alone."""
    kind = kind or default_kind()
    image(kind)
    return {} if kind == default_kind() else {"engines": [kind]}


def org_setup_file() -> str:
    """``E2E_ORG_SETUP``, else the one ``<dir>/org_setup.py`` a build keeps beside this file; "" when there is none.
    Found rather than configured so every lane of that build runs it, whatever script started the lane."""
    named = os.environ.get(ORG_SETUP_ENV, "")
    if named:
        return named
    found = sorted(ORG_SETUP_ROOT.glob("*/org_setup.py"))
    if len(found) > 1:
        raise RuntimeError(f"more than one org setup ({', '.join(map(str, found))}); name one in {ORG_SETUP_ENV}")
    return str(found[0]) if found else ""


def prepare_org(request: Callable[[str, str, Any], Any], kinds: Iterable[str]) -> Callable[[], None]:
    """What the org needs before an engine agent runs one of ``kinds``, and how to put it back: nothing here, since
    every release engine, an ACP agent too, runs with no org setting (``acpOptIn`` ``release``, engine-profiles.ts).
    ``E2E_ORG_SETUP`` (else ``org_setup_file``) names a Python file whose ``prepare(request, kinds)`` does a build's own
    setup and returns its undo; ``request(method, path, body)`` is the signed-in admin's API call."""
    wanted = list(dict.fromkeys(kinds))
    for kind in wanted:
        image(kind)
    path = org_setup_file()
    if not path:
        return lambda: None
    spec = importlib.util.spec_from_file_location("e2e_org_setup", path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"{ORG_SETUP_ENV}={path} is not a Python file")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare(request, wanted)


def main(argv: list[str]) -> int:
    if argv == ["kinds"]:
        print("\n".join(kinds()))
        return 0
    if len(argv) != 2 or argv[0] != "image":
        print("usage: engines.py image <kind> | kinds", file=sys.stderr)
        return 2
    try:
        print(image(argv[1]))
    except KeyError as error:
        print(error.args[0], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
