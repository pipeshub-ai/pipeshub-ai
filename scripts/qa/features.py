"""The feature map: what the product does, the behaviors that must hold, and the tests that prove them.

Feature files come from the checkout under test (scripts/qa/features/*.toml, then each overlay's
scripts/<name>/qa/features/*.toml), so a change that adds a route can add its feature in the same commit.
`drift` compares the map with the product's surfaces (API operations, UI pages, connectors, toolsets);
`coverage` gives every behavior one status from a run's results.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from qa import globs, paths
from qa.config import Config, list_files

FEATURES_DIR = "scripts/qa/features"
OPENAPI = "backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml"
PAGE = "frontend/app/**/page.tsx"
CONNECTORS = "backend/python/app/connectors/sources/"
TOOLSETS = "backend/python/app/agents/actions/"

SURFACE_KINDS = ("routes", "pages", "connectors", "toolsets")
LIST_KEYS = (*SURFACE_KINDS, "code", "behavior")
FEATURE_KEYS = {"id", "title", "flow", "what", "status", *LIST_KEYS}
FEATURE_REQUIRED = ("id", "title", "flow", "what")
BEHAVIOR_KEYS = {"id", "says", "priority", "kind", "tests", "manual"}
BEHAVIOR_REQUIRED = ("id", "says", "priority")
PRIORITIES = ("P0", "P1", "P2")
KINDS = ("function", "security", "upgrade", "performance")
STATUSES = ("draft", "reviewed")
COVERAGE = ("passed", "failing", "flaky", "not-run", "no-test", "manual")

_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_OP_PATH = re.compile(r"^  (/\S*):\s*$")
_OP_METHOD = re.compile(r"^    (" + "|".join(_METHODS) + r"):\s*$")
_SERVER_BASE = re.compile(r"""^\s*-?\s*url:\s*["']?[^"'\s]*?(/api/v\d+)["']?\s*$""")
_PARAM = re.compile(r"\{[^}/]*\}")


def _dirs(root: Path) -> list[Path]:
    return [root / FEATURES_DIR] + [o / "features" for o in paths.overlays(root)]


def load(root: Path) -> dict[str, Any]:
    """The merged map: {"features": [...], "ignore": {kind: [...]}}. An overlay may only extend list keys."""
    features: list[dict[str, Any]] = []
    ignore: dict[str, list[str]] = {k: [] for k in SURFACE_KINDS}
    for i, d in enumerate(_dirs(root)):
        seen: set[str] = set()
        for f in sorted(d.glob("*.toml")) if d.is_dir() else []:
            with f.open("rb") as fh:
                doc = tomllib.load(fh)
            for kind, values in doc.get("ignore", {}).items():
                if kind not in ignore:
                    raise ValueError(f"{f.name}: ignore.{kind} is not one of {', '.join(SURFACE_KINDS)}")
                ignore[kind] += list(values)
            for entry in doc.get("feature", []):
                fid = entry.get("id")
                if fid in seen:
                    raise ValueError(f"{f.name}: duplicate feature id {fid!r}")
                seen.add(fid)
                match = next((x for x in features if x["id"] == fid), None) if i else None
                if match is None:
                    features.append({**entry, "source": str(f.relative_to(root))})
                    continue
                for key, value in entry.items():
                    if key == "id":
                        continue
                    if key not in LIST_KEYS or not isinstance(value, list):
                        raise ValueError(f"feature {fid}: an overlay may only extend the lists {', '.join(LIST_KEYS)}, not {key}")
                    match[key] = [*match.get(key, []), *value]
    return {"features": features, "ignore": ignore}


def check(cfg: Config, fmap: dict[str, Any]) -> list[str]:
    """Shape errors: missing or unknown keys, bad values, unknown flows, duplicate ids."""
    errors: list[str] = []
    ids: Counter[str] = Counter()
    for f in fmap["features"]:
        fid = f.get("id", "?")
        ids[fid] += 1
        errors += [f"feature {fid}: missing {k}" for k in FEATURE_REQUIRED if not f.get(k)]
        errors += [f"feature {fid}: unknown key {k}" for k in sorted(set(f) - FEATURE_KEYS - {"source"})]
        if f.get("flow") and cfg.flow(f["flow"]) is None:
            errors.append(f"feature {fid}: unknown flow {f['flow']!r}")
        if f.get("status", "draft") not in STATUSES:
            errors.append(f"feature {fid}: status must be one of {', '.join(STATUSES)}")
        for r in f.get("routes", []):
            if len(r.split(" ", 1)) != 2:
                errors.append(f"feature {fid}: route {r!r} must be 'METHOD /path'")
        for b in f.get("behavior", []):
            bid = b.get("id", "?")
            ids[bid] += 1
            errors += [f"behavior {bid}: missing {k}" for k in BEHAVIOR_REQUIRED if not b.get(k)]
            errors += [f"behavior {bid}: unknown key {k}" for k in sorted(set(b) - BEHAVIOR_KEYS)]
            if b.get("priority") and b["priority"] not in PRIORITIES:
                errors.append(f"behavior {bid}: priority must be one of {', '.join(PRIORITIES)}")
            if b.get("kind", "function") not in KINDS:
                errors.append(f"behavior {bid}: kind must be one of {', '.join(KINDS)}")
    errors += [f"duplicate id {i!r}" for i, n in sorted(ids.items()) if n > 1]
    return errors


def behaviors(fmap: dict[str, Any]):
    for f in fmap["features"]:
        for b in f.get("behavior", []):
            yield f, b


# --- surfaces ------------------------------------------------------------------

def openapi_routes(text: str) -> list[str]:
    """'METHOD /api/v1/path' for every operation, the server's base path in front."""
    lines = text.splitlines()
    base = next((m.group(1) for line in lines if (m := _SERVER_BASE.match(line))), "")
    routes, current, in_paths = [], None, False
    for line in lines:
        if line.startswith("paths:"):
            in_paths = True
            continue
        if in_paths and line[:1] not in ("", " ", "#"):
            break
        if not in_paths:
            continue
        if m := _OP_PATH.match(line):
            current = m.group(1)
        elif current and (m := _OP_METHOD.match(line)):
            routes.append(f"{m.group(1).upper()} {base}{current}")
    return sorted(set(routes))


def _top_dirs(files: list[str], prefix: str) -> list[str]:
    """Directories directly under prefix that hold at least one file, skipping private ones (_x, .x)."""
    return sorted({p[len(prefix):].split("/", 1)[0] for p in files
                   if p.startswith(prefix) and "/" in p[len(prefix):] and p[len(prefix)] not in "_."})


def surfaces(root: Path) -> dict[str, list[str]]:
    files = list_files(root)
    spec = root / OPENAPI
    return {
        "routes": openapi_routes(spec.read_text(encoding="utf-8")) if spec.is_file() else [],
        "pages": [p for p in files if globs.match(p, PAGE)],
        "connectors": _top_dirs(files, CONNECTORS),
        "toolsets": _top_dirs(files, TOOLSETS),
    }


def _route_matches(pattern: str, route: str) -> bool:
    """A route pattern is 'METHOD /path'; METHOD may be *, path params match any {param}, and path globs work."""
    pm, _, pp = pattern.partition(" ")
    rm, _, rp = route.partition(" ")
    return (pm == "*" or pm.upper() == rm) and globs.match(_PARAM.sub("{}", rp), _PARAM.sub("{}", pp.strip()))


def _matches(kind: str, pattern: str, value: str) -> bool:
    if kind == "routes":
        return _route_matches(pattern, value)
    if kind == "pages":
        return globs.match(value, pattern)
    return fnmatch.fnmatchcase(value, pattern)


# --- drift and coverage ------------------------------------------------------------

def resolve(ref: str, rows: list[dict[str, Any]]) -> list[str]:
    """Catalog ids a behavior's test reference names: a TC id, a catalog id, or a file-level catalog id."""
    return [r["id"] for r in rows if r.get("tc") == ref or r["id"] == ref or r["id"].startswith(ref + "::")]


def changed_files(root: Path, since: str) -> list[str]:
    out = subprocess.run(["git", "diff", "--name-only", f"{since}...HEAD"], cwd=root, check=True,
                         capture_output=True, text=True).stdout
    return [line for line in out.splitlines() if line]


def drift(root: Path, fmap: dict[str, Any], rows: list[dict[str, Any]], since: str | None = None) -> dict[str, Any]:
    """What the map misses (unclaimed), what it names that is gone (stale, dead-test), and what changed."""
    surf = surfaces(root)
    files = list_files(root)
    unclaimed, stale, dead = [], [], []
    for kind in SURFACE_KINDS:
        patterns = [p for f in fmap["features"] for p in f.get(kind, [])] + fmap["ignore"][kind]
        unclaimed += [{"kind": kind, "value": v} for v in surf[kind] if not any(_matches(kind, p, v) for p in patterns)]
        for f in fmap["features"]:
            stale += [{"feature": f["id"], "kind": kind, "value": p} for p in f.get(kind, [])
                      if not any(_matches(kind, p, v) for v in surf[kind])]
    for f in fmap["features"]:
        stale += [{"feature": f["id"], "kind": "code", "value": p} for p in f.get("code", [])
                  if not any(globs.match(x, p) for x in files)]
    for _, b in behaviors(fmap):
        dead += [{"behavior": b["id"], "test": t} for t in b.get("tests", []) if not resolve(t, rows)]
    changed = []
    if since:
        touched = changed_files(root, since)
        changed = [f["id"] for f in fmap["features"]
                   if any(globs.match_any(x, f.get("code", []) + f.get("pages", [])) for x in touched)]
    return {"surfaces": {k: len(v) for k, v in surf.items()}, "unclaimed": unclaimed, "stale": stale,
            "dead_test": dead, "changed": changed}


def _status(b: dict[str, Any], ids: list[str], results: dict[str, dict[str, Any]]) -> tuple[str, str]:
    if b.get("manual"):
        return "manual", b["manual"]
    if not ids:
        return "no-test", ""
    got = {i: results.get(i) for i in ids}
    bad = [i for i, t in got.items() if t and t["outcome"] in ("fail", "error")]
    if bad:
        return "failing", ", ".join(bad)
    missing = [i for i, t in got.items() if not t or t["outcome"] in ("not_run", "skip")]
    if missing:
        t = got[missing[0]]
        why = (t.get("kind") or t["outcome"]) if t else "not in this run"
        return "not-run", f"{missing[0]}: {why}"
    if any(t["outcome"] == "flaky" for t in got.values()):
        return "flaky", ", ".join(i for i, t in got.items() if t["outcome"] == "flaky")
    return "passed", ""


def coverage(fmap: dict[str, Any], rows: list[dict[str, Any]], results: dict[str, Any] | None) -> dict[str, Any]:
    """One status per behavior; a behavior passes only when every test it names passed in these results."""
    by_id = {t["id"]: t for t in (results or {}).get("tests", [])}
    items = []
    for f, b in behaviors(fmap):
        status, detail = _status(b, sorted({i for t in b.get("tests", []) for i in resolve(t, rows)}), by_id)
        items.append({"feature": f["id"], "flow": f["flow"], "draft": f.get("status", "draft") == "draft",
                      "behavior": b["id"], "says": b["says"], "priority": b["priority"],
                      "kind": b.get("kind", "function"), "status": status, "detail": detail})
    counts = {p: {s: 0 for s in COVERAGE} for p in PRIORITIES}
    for it in items:
        counts[it["priority"]][it["status"]] += 1
    return {"features": len(fmap["features"]), "draft": sum(f.get("status", "draft") == "draft" for f in fmap["features"]),
            "behaviors": items, "counts": counts}


def render(cov: dict[str, Any], drift_doc: dict[str, Any] | None = None, cap: int = 40) -> str:
    """The report section: counts by priority, every P0 behavior that did not pass, and the drift totals."""
    if not cov["features"]:
        return ""
    lines = ["### Feature coverage", "",
             f"{cov['features']} features ({cov['draft']} draft), {len(cov['behaviors'])} behaviors.", "",
             "| Priority | " + " | ".join(COVERAGE) + " |", "|---|" + "---|" * len(COVERAGE)]
    lines += [f"| {p} | " + " | ".join(str(cov["counts"][p][s]) for s in COVERAGE) + " |" for p in PRIORITIES]
    open_p0 = [b for b in cov["behaviors"] if b["priority"] == "P0" and b["status"] != "passed"]
    if open_p0:
        lines += ["", f"P0 behaviors that did not pass ({len(open_p0)}):", ""]
        lines += [f"- **{b['status']}** `{b['behavior']}`: {b['says']}" + (f" ({b['detail']})" if b["detail"] else "")
                  for b in open_p0[:cap]]
        if len(open_p0) > cap:
            lines.append(f"- … and {len(open_p0) - cap} more in features-coverage.json")
    if drift_doc:
        lines += ["", "Map drift: " + ", ".join(f"{len(drift_doc[k])} {k.replace('_', '-')}"
                                                for k in ("unclaimed", "stale", "dead_test")) + "."]
    return "\n".join(lines) + "\n"
