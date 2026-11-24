"""qa improve: after a run, find the change's weak tests and add stronger and missing ones.

Three phases, which CI runs in separate jobs as it does for `/qa tests --implement`:

check   runs the tests. Each unit test that covers a changed Python file runs on head and then
        against the reverted change and up to N mutants of its changed lines (prove's rules,
        lint off). A test that passes every variant is weak; the mutants it survived are kept.
draft   asks the model and checks each reply; it writes no test file and runs nothing. The model
        returns a stronger companion for each weak test and tests for missing scenarios of the
        changed files. The engine picks every path: a new file next to the existing test
        (test_<name>_qa.py), never an edit of a file a person wrote. A reply must pass
        generate.check_content, the same checks as /qa tests --implement.
prove   writes each drafted file and keeps it only when prove proves it; any other is deleted.
        Nothing is committed.

The check output can come from a job that ran the PR's code, so draft keeps only ids of its own
candidate list and mutants on their covered files with prove's operators.
"""

from __future__ import annotations

import json
import posixpath
import re
import time
from pathlib import Path
from typing import Any

from qa import catalog, config, generate, globs, planner, prove
from qa.config import Config
from qa.impact import mirror_test
from qa.validate import clean_text

# The engine phases (check, model, prove) fit in about 45 minutes with CI's 15-minute model timeout.
MAX_CHECKED, CHECK_MUTANTS, MAX_NEW, MINUTES = 20, 3, 10, 10.0
MAX_FILE, MAX_FILES_TEXT, MAX_DIFF, MAX_UNTRUSTED = 20_000, 60_000, 40_000, 1_000_000
REPLY = re.compile(r"[0-9]{1,3}-llm\.json")
SHA = re.compile(r"[0-9a-f]{40}")
NEXT = "next: review the kept tests with git diff and commit them yourself; nothing was committed"


def _unit_suite(cfg: Config, path: str) -> dict | None:
    """The suite of a path when it is a unit suite: pytest, a Python mirror, no stack."""
    s = catalog.suite_of(path, cfg)
    if s and s["family"] == "pytest" and not s.get("stack") and any(m.get("style") == "py" for m in s.get("mirror", [])):
        return s
    return None


def changed_sources(cfg: Config, changeset: dict) -> list[str]:
    # A symlinked source is never a candidate: its text would be read for the model.
    return [f["path"] for f in changeset["files"]
            if f["path"].endswith(".py") and f["status"] != "D" and catalog.suite_of(f["path"], cfg) is None
            and not (cfg.root / f["path"]).is_symlink()]


def candidates(cfg: Config, rows: list[dict], sources: list[str], cap: int) -> list[dict]:
    """Active unit tests that cover a changed source: the suite's mirror of it, or a covers: tag naming it.
    Each comes back with covers set to those changed sources."""
    out = []
    for r in rows:
        suite = _unit_suite(cfg, r["path"])
        if not suite or r["status"] != "active" or "service:stack" in r["needs"]:
            continue
        hit = {f for f in sources for m in suite["mirror"] if m.get("style") == "py" and mirror_test(f, m) == r["path"]}
        hit |= {c for c in r.get("covers", []) if c in sources}
        if hit:
            out.append({**r, "covers": sorted(hit)})
    return out[:cap]


def check(cfg: Config, rows: list[dict], cands: list[dict], base: str, out: Path, mutants: int, minutes: float) -> dict:
    deadline = time.monotonic() + minutes * 60
    index = catalog.index(rows)
    result: dict[str, Any] = {"schema": "qa-improve-check/v1", "weak": [], "strong": [], "inconclusive": [], "unchecked": []}
    for row in cands:
        # Written after each test, so a job that times out keeps what finished.
        (out / "improve-check.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        if time.monotonic() > deadline:
            result["unchecked"].append({"id": row["id"], "reason": "time budget spent"})
            continue
        v = prove.prove(cfg, row, out / "improve", base=base, repeat=1, max_mutants=mutants, rows=index, lint=False)
        if v["verdict"] == "proven":
            result["strong"].append({"id": row["id"], "reason": v["reason"]})
        elif v["reason"] == prove.SURVIVED:
            result["weak"].append({"id": row["id"], "revert": (v.get("revert") or {}).get("outcome") == "pass",
                                   "survived": [{k: m[k] for k in ("file", "line", "op")}
                                                for m in v["mutants"] if m.get("outcome") == "pass"]})
        else:
            result["inconclusive"].append({"id": row["id"], "reason": v["reason"]})
    (out / "improve-check.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result


def read_untrusted(path: str | None) -> Any:
    """The weak list a check job wrote. Anything unreadable is no list."""
    try:
        return json.loads(Path(path).read_bytes()[:MAX_UNTRUSTED]) if path else []
    except (OSError, ValueError):
        return []


def weak_from(untrusted: Any, cands: dict[str, dict]) -> list[dict]:
    """Only candidate ids, each once, with mutants on its own covered files and prove's operators."""
    weak: list[dict] = []
    for w in untrusted if isinstance(untrusted, list) else []:
        if not isinstance(w, dict) or w.get("id") not in cands or any(x["id"] == w["id"] for x in weak):
            continue
        row = cands[w["id"]]
        survived = [{"file": m["file"], "line": m["line"], "op": m["op"]} for m in w.get("survived") or []
                    if isinstance(m, dict) and m.get("file") in row["covers"] and type(m.get("line")) is int
                    and m.get("op") in prove.OPS][:CHECK_MUTANTS]
        if survived or w.get("revert") is True:
            weak.append({"id": row["id"], "path": row["path"], "covers": row["covers"],
                         "revert": w.get("revert") is True, "survived": survived})
    return weak


def _new_path(cfg: Config, target: str, taken: set[str]) -> str | None:
    """test_x.py -> test_x_qa.py (or _qa_2, ...): a free path inside the unit suite's directory."""
    stem = target.removesuffix(".py") + "_qa"
    path, n = stem + ".py", 1
    while path in taken or (cfg.root / path).exists() or (cfg.root / path).is_symlink():
        n += 1
        path = f"{stem}_{n}.py"
    if not (generate.plain_path(path) and _unit_suite(cfg, path) and generate._inside_suite(cfg, path, planned=False)):
        return None
    taken.add(path)
    return path


def _test_id(cfg: Config, path: str) -> str:
    suite = catalog.suite_of(path, cfg)
    return f"{catalog.PREFIX[suite['family']]}:{catalog.rel_to(path, suite.get('cwd', '.'))}"


def _mirror(cfg: Config, source: str) -> str | None:
    return next((t for s in cfg.suites if s["family"] == "pytest" for m in s.get("mirror", [])
                 if m.get("style") == "py" and (t := mirror_test(source, m)) and _unit_suite(cfg, t)), None)


def draft_input(cfg: Config, changeset: dict, diff: str, cands: list[dict], weak: list[dict], max_new: int) -> dict:
    sources = changed_sources(cfg, changeset)
    files, room = [], MAX_FILES_TEXT
    for p in sources + [w["path"] for w in weak]:
        if room <= 0:
            break
        text = generate.tree_text(cfg.root, p, min(MAX_FILE, room))
        if text is not None:
            room -= len(text)
            files.append({"path": p, "text": text})
    return {
        "diff": diff[:MAX_DIFF],
        "modules": [{"id": m["id"], "title": m.get("title", m["id"]), "paths": m.get("paths", [])}
                    for m in cfg.modules if any(globs.match_any(f, m.get("paths", [])) for f in sources)],
        "sources": sources,
        "tests": [{"id": r["id"], "path": r["path"], "covers": r["covers"]} for r in cands],
        "weak": [{k: w[k] for k in ("id", "path", "revert", "survived")} for w in weak],
        "files": files,
        "max_new": max_new,
    }


def draft(cfg: Config, rows: list[dict], changeset: dict, diff: str, untrusted_weak: Any, out: Path,
          adapter: str, timeout: int, max_new: int, max_checked: int) -> dict:
    sources = changed_sources(cfg, changeset)
    cands = candidates(cfg, rows, sources, max_checked)
    weak = weak_from(untrusted_weak, {r["id"]: r for r in cands})
    work = out / "improve"
    work.mkdir(parents=True, exist_ok=True)
    (work / "improve-input.json").write_text(json.dumps(draft_input(cfg, changeset, diff, cands, weak, max_new), indent=2),
                                             encoding="utf-8")
    status = planner.ask(cfg.root, adapter, "improve", work / "improve-input.json", work / "improve-llm.json", timeout=timeout)
    reply = json.loads((work / "improve-llm.json").read_text(encoding="utf-8"))
    result: dict[str, Any] = {"schema": "qa-improve/v1", "base": changeset["base"], "head": changeset["head"],
                              "model": {k: status.get(k) for k in ("adapter", "status", "error")},
                              "weak": weak, "drafted": [], "dropped": []}
    if status.get("status") == "skipped":
        result["skipped"] = "no model is configured"
    elif status.get("status") != "ok":
        result["skipped"] = f"the model failed: {status.get('error') or 'no reply'}"
    taken: set[str] = set()
    companions: set[str] = set()
    missing = 0
    for t in reply.get("tests", [])[:len(weak) + max_new]:
        title = clean_text(t.get("title"), 120) or "untitled"

        def drop(why: str) -> None:
            result["dropped"].append({"title": title, "why": why[:500]})

        if t.get("level") != "unit":
            drop("needs a stack: only unit tests are proven here")
            continue
        if t.get("kind") == "stronger":
            w = next((w for w in weak if w["id"] == t.get("for")), None)
            if w is None or w["id"] in companions:
                drop("not a weak test of this run, or its second companion")
                continue
            target, covers, of = w["path"], w["covers"], w["id"]
        elif t.get("kind") == "missing":
            covers = [c for c in t.get("covers") or [] if c in sources]
            target, of = next((m for c in covers if (m := _mirror(cfg, c))), None), None
            if missing >= max_new or target is None:
                drop("over the cap" if missing >= max_new else "covers no changed Python file a unit suite mirrors")
                continue
        else:
            drop("unknown kind")
            continue
        path = _new_path(cfg, target, taken)
        if path is None:
            drop("no safe path for the new file")
            continue
        content = t.get("content") if isinstance(t.get("content"), str) else ""
        problems = generate.check_content(None, content)
        if problems:
            drop("; ".join(problems))
            continue
        name = f"{len(result['drafted']) + 1}-llm.json"
        (work / name).write_text(json.dumps({"content": content}), encoding="utf-8")
        result["drafted"].append({"id": _test_id(cfg, path), "kind": t["kind"], "for": of, "path": path,
                                  "covers": covers, "title": title, "reply": name})
        if of:
            companions.add(of)
        else:
            missing += 1
    _write(out, result)
    return result


def prove_drafts(cfg: Config, out: Path, minutes: float, repeat: int | None = None, max_mutants: int | None = None) -> dict:
    """Write each drafted file, prove it, delete it unless proven. Re-checks what draft checked: the
    improve.json it reads may come from another job."""
    deadline = time.monotonic() + minutes * 60
    data = json.loads((out / "improve.json").read_text(encoding="utf-8"))
    data["kept"], data["removed"] = [], []
    base = data.get("base") if isinstance(data.get("base"), str) and SHA.fullmatch(data["base"]) else None
    rows = catalog.index(catalog.build(cfg))
    for d in data.get("drafted", []):
        test_id, path, reply = d.get("id"), d.get("path"), d.get("reply")
        why = None
        if time.monotonic() > deadline:
            why = "time budget spent"
        elif not base:
            why = "no base commit"
        elif not (isinstance(path, str) and generate.plain_path(path) and _unit_suite(cfg, path)
                  and generate._inside_suite(cfg, path, planned=False) and _test_id(cfg, path) == test_id):
            why = "unsafe path"
        elif (cfg.root / path).exists() or (cfg.root / path).is_symlink():
            why = "the path is taken"
        elif not (isinstance(reply, str) and REPLY.fullmatch(reply) and (out / "improve" / reply).is_file()):
            why = "no draft for this id"
        if why:
            data["removed"].append({"id": str(test_id), "reason": why})
            continue
        content = json.loads((out / "improve" / reply).read_text(encoding="utf-8")).get("content") or ""
        problems = generate.check_content(None, content)
        if problems:
            data["removed"].append({"id": test_id, "reason": "; ".join(problems)[:500]})
            continue
        full = cfg.root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(full, "x", encoding="utf-8") as f:
                f.write(content)
        except FileExistsError:  # a dangling symlink or a race: never touch a file this run did not create
            data["removed"].append({"id": test_id, "reason": "the path is taken"})
            continue
        kept = False
        try:
            back = catalog.read_rows(cfg, path)
            if [r["id"] for r in back] != [test_id]:
                data["removed"].append({"id": test_id, "reason": "the catalog does not read it as one test file"})
                continue
            covers = [c for c in d.get("covers") or [] if isinstance(c, str) and generate.plain_path(c)]
            row = {**back[0], "covers": covers}
            v = prove.prove(cfg, row, out / "improve", base=base, repeat=repeat, max_mutants=max_mutants,
                            rows={**rows, test_id: row})
            kept = v["verdict"] == "proven"
            data["kept" if kept else "removed"].append({"id": test_id, "reason": v["reason"]})
        finally:
            if not kept:
                full.unlink(missing_ok=True)
    config.clear_cache()
    _write(out, data)
    return data


def _write(out: Path, data: dict) -> None:
    (out / "improve.json").write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    (out / "improve.md").write_text(render(data), encoding="utf-8")


def render(data: dict) -> str:
    """The body of the Improvements section; the caller adds the heading."""
    model = data.get("model") or {}
    lines = [f"Model: {clean_text(model.get('adapter'), 60)}, {clean_text(model.get('status'), 60)}."]
    if data.get("skipped"):
        lines.append(f"Skipped: {clean_text(data['skipped'], 300)}.")
    lines += ["", "#### Weak tests (pass with the change broken)", ""]
    for w in data.get("weak", []):
        how = [f"`{m['op']}` at {clean_text(m['file'], 200)}:{m['line']}" for m in w["survived"]]
        how += ["the change reverted"] if w.get("revert") else []
        lines.append(f"- {clean_text(w['id'], 300)}: passes with " + ", ".join(how))
    lines += [] if data.get("weak") else ["None found."]
    lines += ["", "#### Drafted", ""]
    for d in data.get("drafted", []):
        what = f"stronger companion of {clean_text(d['for'], 300)}" if d.get("for") else "missing scenario"
        lines.append(f"- {clean_text(d['path'], 300)} ({what}): {clean_text(d['title'], 120)}")
    lines += [] if data.get("drafted") else ["None."]
    for key, title in (("kept", "Kept (proven, uncommitted)"), ("removed", "Not kept")):
        if key in data:
            lines += ["", f"#### {title}", ""]
            lines += [f"- {clean_text(e['id'], 300)}: {clean_text(e['reason'], 300)}" for e in data[key]] or ["None."]
    lines += ["", "#### Dropped drafts", ""]
    lines += [f"- {clean_text(d['title'], 120)}: {clean_text(d['why'], 300)}" for d in data.get("dropped", [])] or ["None."]
    return "\n".join(lines) + "\n"
