"""Parse a `/qa ...` PR comment into a dispatch request, checking permission.

The comment body is untrusted: arguments must match fixed patterns and the
caller's permission comes from the GitHub API, never from the comment.
"""

from __future__ import annotations

import re
from typing import Any

SHA = r"[0-9a-f]{7,40}"
# A catalog id such as py:tests/unit/x/test_y.py or it:area/test_y.py::test_z; at most ten per command.
TEST_ID = r"[a-z][a-z0-9-]{0,15}:[A-Za-z0-9_./:-]{1,300}"
LEVEL = {"none": 0, "read": 1, "triage": 1, "write": 2, "maintain": 3, "admin": 4}
LEVEL_NAME = {k: k for k in LEVEL}

COMMANDS: dict[str, dict[str, Any]] = {
    "plan": {"min": "write", "mode": "plan", "args": r""},
    "run": {"min": "write", "mode": "pr", "args": r""},
    "review": {"min": "write", "mode": "pr", "args": r""},
    "commit": {"min": "write", "mode": "pr", "args": r"(?P<head>[0-9a-f]{40})"},
    "missing": {"min": "write", "mode": "missing", "args": r""},
    "since": {"min": "write", "mode": "since", "args": rf"(?P<base>{SHA})"},
    "range": {"min": "write", "mode": "range", "args": rf"(?P<base>{SHA})(?:\.\.(?P<head>{SHA}))?"},
    "full": {"min": "write", "mode": "full", "args": r""},
    "agent": {"min": "write", "mode": "pr", "args": r""},
    # Pull requests to stack on the default branch and test together, in this order.
    "combine": {"min": "write", "mode": "pr", "args": r"(?P<prs>#?\d{1,7}(?:\s+#?\d{1,7}){1,7})(?P<open_pr>\s+--open-pr)?"},
    "tests": {"min": "write", "mode": "tests",
              "args": rf"(?P<module>[a-z][a-z0-9-]{{0,63}})|--implement\s+(?P<implement>{TEST_ID}(?:,{TEST_ID}){{0,9}})"},
}
PROFILE = re.compile(r"\s+--profile\s+(fast|standard|conservative|weekly)(?![\w-])")
TRUSTED = re.compile(rf"\s+--trusted(?:\s+(?P<sha>{SHA})(?![0-9a-zA-Z]))?(?![\w-])")


class CommandError(ValueError):
    pass


def parse(body: str, permission: str, is_admin_team: bool = False, agent_default: bool = False) -> dict[str, Any]:
    """agent_default (the repository variable QA_AGENT): plain /qa runs the QA agent instead of the plan."""
    first = (body or "").strip().splitlines()[0] if (body or "").strip() else ""
    if not first.startswith("/qa"):
        raise CommandError("not a /qa command")
    text = first[3:]
    profile = None
    if m := PROFILE.search(text):
        profile = m.group(1)
        text = PROFILE.sub("", text)
    tm = TRUSTED.search(text)
    trusted = bool(tm)
    if tm and len(tm.group("sha") or "") != 40:
        raise CommandError("--trusted needs the full 40-character commit SHA you reviewed")
    text = TRUSTED.sub("", text).strip()
    name, _, rest = text.partition(" ")
    name = name or "run"
    if name not in COMMANDS:
        # Never echo the comment back: the reply is posted on the PR.
        raise CommandError(f"unknown command; use one of: {', '.join(COMMANDS)}")
    spec = COMMANDS[name]
    if profile and name in ("tests", "agent", "combine"):
        raise CommandError(f"/qa {name} takes no --profile")
    if trusted and name == "combine":
        raise CommandError("/qa combine runs pull requests from this repository only; it takes no --trusted")
    m = re.fullmatch(spec["args"], rest.strip())
    if not m:
        raise CommandError(f"/qa {name} takes {_usage(name)}")
    level = LEVEL.get(permission, 0)
    needed = "admin" if (trusted or profile == "conservative" and name == "full") else spec["min"]
    if level < LEVEL[needed] and not (needed == "admin" and is_admin_team and level >= LEVEL["write"]):
        raise CommandError(f"/qa {name}{' --trusted' if trusted else ''} needs {needed} permission; you have {LEVEL_NAME.get(permission, 'none')}")
    out = {"command": name, "mode": spec["mode"], "profile": profile or ("full" if name == "full" else "standard"), "trusted": trusted,
           "plan_only": name in ("plan", "missing"), "review": name == "review"}
    out.update({k: v for k, v in m.groupdict().items() if v})
    out["agent"] = name in ("agent", "combine") or (name == "run" and agent_default and not profile)
    if name == "combine":
        prs = [int(x) for x in re.findall(r"\d+", out["prs"])]
        if len(set(prs)) != len(prs):
            raise CommandError("/qa combine lists a pull request twice")
        out["prs"] = " ".join(map(str, prs))
        out["open_pr"] = bool(out.get("open_pr"))
    if tm:
        out["trusted_head"] = tm.group("sha")
    return out


def _usage(name: str) -> str:
    return {
        "commit": "the full 40-character commit sha: /qa commit <sha>",
        "since": "one commit sha: /qa since <sha>",
        "range": "a range: /qa range <sha> or /qa range <sha>..<sha>",
        "tests": "a module id or test ids: /qa tests <module> or /qa tests --implement <ID[,ID...]>",
        "combine": "2 to 8 pull request numbers, in stacking order: /qa combine #12 #15 [--open-pr]",
    }.get(name, "no arguments")
