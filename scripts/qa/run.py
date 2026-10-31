"""`qa run`: resolve, preflight, stack, execute, collect, teardown.

Every planned test ends with an outcome: pass | fail | error | flaky | skip |
not_run. A not_run always carries the reason, so nothing is skipped silently.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qa import junit
from qa import shard as shard_mod
from qa.catalog import rel_to
from qa.config import Config
from qa import stack as stack_mod
from qa.stack import Stack

STACK_PORTS = (3000, 8000, 8088, 8091)
# One broken test file must not stop every other selected file from running.
PYTEST_FLAGS = ["-p", "no:cacheprovider", "--continue-on-collection-errors"]
DEFAULT_TIMEOUT = {"it-pytest": 4 * 3600, "playwright": 3600}


@dataclass
class Invocation:
    label: str
    suite: str
    family: str
    cwd: str
    cmd: list[str]
    ids: list[str]
    junit: Path | None
    env: dict[str, str] = field(default_factory=dict)
    needs_stack: bool = False
    graph_db: str | None = None
    modules: dict[str, str] = field(default_factory=dict)
    unavailable: tuple[str, str] | None = None


def _split(env_name: str, default: list[str]) -> list[str]:
    value = os.environ.get(env_name)
    return shlex.split(value) if value else default


def python_cmd() -> list[str]:
    return _split("QA_PYTHON", [sys.executable])


def it_python_cmd(root: Path) -> list[str]:
    venv = root / "integration-tests/.venv/bin/python"
    return _split("QA_IT_PYTHON", [str(venv)] if venv.is_file() else python_cmd())


def npx_cmd() -> list[str]:
    return _split("QA_NPX", ["npx"])


def _glob_root(pattern: str, cwd: str) -> str:
    head = []
    for part in rel_to(pattern, cwd).split("/"):
        if any(c in part for c in "*?["):
            break
        head.append(part)
    return "/".join(head) or "."


def _mocha_requires(path: Path) -> list[str]:
    requires, inside = [], False
    for line in path.read_text(encoding="utf-8").splitlines():
        if re.match(r"^require\s*:", line):
            inside = True
            continue
        if inside:
            m = re.match(r"^\s+-\s+['\"]?([^'\"]+)['\"]?\s*$", line)
            if m:
                requires.append(m.group(1))
            elif line.strip():
                inside = False
    return requires


def _mocha_scalars(path: Path) -> dict[str, Any]:
    """parallel/jobs/timeout from a mocharc. Parallel matters: it isolates a spec that fails to load."""
    found: dict[str, Any] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(parallel|jobs|timeout)\s*:\s*['\"]?([^'\"#\s]+)", line)
        if m:
            v = m.group(2)
            found[m.group(1)] = v == "true" if v in ("true", "false") else int(v) if v.isdigit() else v
    return found


def _mark(not_run: dict[str, tuple[str, str]], ids, kind: str, reason: str) -> None:
    """Record why tests did not run; the first reason wins."""
    for i in ids:
        not_run.setdefault(i, (kind, reason))


def resolve(cfg: Config, plan: dict[str, Any], rows: dict[str, dict[str, Any]], out: Path) -> tuple[list[Invocation], dict[str, tuple[str, str]]]:
    """Group the plan's tests by suite into runner commands; unknown ids are not_run."""
    not_run: dict[str, tuple[str, str]] = {}
    by_suite: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in plan["tests"]:
        row = rows.get(t["id"])
        if row is None:
            _mark(not_run, [t["id"]], "no_result", "not in the catalog")
            continue
        by_suite[row["suite"]].append({**row, "grep": t.get("grep")})
    suite_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows.values():
        suite_rows[r["suite"]].append(r)

    invocations: list[Invocation] = []
    for suite_id, selected in sorted(by_suite.items()):
        suite = cfg.suite(suite_id)
        cwd = suite.get("cwd", ".")
        fam = suite["family"]
        files = sorted({rel_to(r["path"], cwd) for r in selected})
        ids = [r["id"] for r in selected]
        whole = {r["id"] for r in suite_rows[suite_id] if r.get("status") != "planned"} <= set(ids)
        targets = sorted({_glob_root(g, cwd) for g in suite["tests"]}) if whole else files
        jx = out / f"junit-{suite_id}.xml"
        if fam == "unittest":
            # Stdlib-only and needs git: always the host interpreter, never QA_PYTHON.
            cmd = _split("QA_SCRIPTS_PYTHON", [sys.executable]) + ["-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"]
            invocations.append(Invocation(suite_id, suite_id, fam, cwd, cmd, ids, None))
        elif fam == "pytest":
            cmd = python_cmd() + ["-m", "pytest", *targets, *PYTEST_FLAGS, "-q", f"--junitxml={jx}"] + _split("QA_PYTEST_ARGS", [])
            invocations.append(Invocation(suite_id, suite_id, fam, cwd, cmd, ids, jx, modules=junit.module_names(files)))
        elif fam == "it-pytest":
            node_ids = _it_node_ids(selected, suite_rows[suite_id], cwd)
            graphs = plan["stack"]["graph_db"] if suite.get("stack") else [None]
            for g in graphs:
                label = f"{suite_id}-{g}" if g else suite_id
                gx = out / f"junit-{label}.xml"
                cmd = it_python_cmd(cfg.root) + ["-m", "pytest", *node_ids, *PYTEST_FLAGS, f"--junitxml={gx}"]
                workers = os.environ.get("QA_IT_WORKERS")
                if workers and suite.get("stack"):
                    cmd += ["-n", workers, "--dist", "loadgroup"]
                env = {"TEST_GRAPH_DB_TYPE": g} if g else {}
                invocations.append(Invocation(label, suite_id, fam, cwd, cmd, ids, gx, env=env, needs_stack=bool(suite.get("stack")), graph_db=g, modules=junit.module_names(files)))
        elif fam == "mocha":
            config = out / f"mocharc-{suite_id}.json"
            base = cfg.root / cwd / suite.get("mocha_config", ".mocharc.yaml")
            spec = {
                "require": _mocha_requires(base) if base.is_file() else [],
                "spec": targets if not whole else [rel_to(g, cwd) for g in suite["tests"]],
                "extension": ["ts"], "timeout": 10000, "recursive": True, "exit": True,
                **(_mocha_scalars(base) if base.is_file() else {}),
            }
            out.mkdir(parents=True, exist_ok=True)
            config.write_text(json.dumps(spec, indent=2), encoding="utf-8")
            cmd = npx_cmd() + ["mocha", "--config", str(config), "--reporter", "xunit", "--reporter-option", f"output={jx}"]
            env = {"TS_NODE_PROJECT": suite.get("ts_project", "tsconfig.test.json")}
            invocations.append(Invocation(suite_id, suite_id, fam, cwd, cmd, ids, jx, env=env))
        elif fam == "vitest":
            cmd = npx_cmd() + ["vitest", "run", *([] if whole else files), "--reporter=junit", f"--outputFile={jx}"]
            if suite.get("vitest_config"):
                cmd += ["--config", suite["vitest_config"]]
            invocations.append(Invocation(suite_id, suite_id, fam, cwd, cmd, ids, jx))
        elif fam == "npm-script":
            invocations.append(Invocation(suite_id, suite_id, fam, cwd, ["npm", "run", suite["script"]], ids, None))
        elif fam == "playwright":
            groups: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
            for r in selected:
                groups[r.get("grep")].append(r)
            for grep, rs in groups.items():
                label = f"{suite_id}-{grep.strip('@')}" if grep else suite_id
                gx = out / f"junit-{label}.xml"
                cmd = npx_cmd() + ["playwright", "test", *sorted({rel_to(r["path"], cwd) for r in rs}), "--reporter=junit"]
                if grep:
                    cmd += ["--grep", grep]
                env = {"PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(gx), "PLAYWRIGHT_NO_SERVER": "true"}
                invocations.append(Invocation(label, suite_id, fam, cwd, cmd, [r["id"] for r in rs], gx, env=env, needs_stack=True))
        else:
            for i in ids:
                _mark(not_run, [i], "toolchain", f"no runner for family {fam}")
    return invocations, not_run


def _it_node_ids(selected: list[dict[str, Any]], all_rows: list[dict[str, Any]], cwd: str) -> list[str]:
    """Ordered lifecycle modules run whole; a fully selected module runs as a file."""
    by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in all_rows:
        by_file[r["path"]].append(r)
    chosen: dict[str, set[str]] = defaultdict(set)
    for r in selected:
        chosen[r["path"]].add(r["id"])
    out = []
    for path, ids in sorted(chosen.items()):
        rel = rel_to(path, cwd)
        module_rows = by_file[path]
        if any(r.get("ordered") for r in module_rows) or ids >= {r["id"] for r in module_rows}:
            out.append(rel)
        else:
            out.extend(sorted(i.split(":", 1)[1] for i in ids))
    return out


# --- preflight ------------------------------------------------------------------

def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def _probe(cmd: list[str], cwd: Path) -> bool:
    try:
        return subprocess.run(cmd, check=False, cwd=cwd, capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _unset_env(need: str) -> str | None:
    """`A|B+C` is met by A, or by B and C together; None when met."""
    options = [o.split("+") for o in need.split("|")]
    if any(all(os.environ.get(v) for v in o) for o in options):
        return None
    if len(options) == 1:
        return ", ".join(v for v in options[0] if not os.environ.get(v))
    return "one of " + " | ".join("+".join(o) for o in options)


def preflight(cfg: Config, invs: list[Invocation], rows: dict[str, dict[str, Any]], stack_mode: str) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Per-test not_run (kind, reason), plus fatal infra problems for the stack."""
    not_run: dict[str, tuple[str, str]] = {}
    fatal: list[str] = []
    cache: dict[tuple, bool] = {}
    for inv in invs:
        cwd = cfg.root / inv.cwd
        if inv.needs_stack and stack_mode == "none":
            inv.unavailable = ("scope", "needs the integration stack (run with --stack managed or external)")
        elif inv.family in ("pytest", "it-pytest"):
            key = (tuple(inv.cmd[: inv.cmd.index("-m")]), inv.cwd)
            if key not in cache:
                cache[key] = _probe(list(key[0]) + ["-c", "import pytest"], cwd)
            if not cache[key]:
                inv.unavailable = ("toolchain", f"pytest is not importable by {' '.join(key[0])} (set QA_PYTHON / QA_IT_PYTHON)")
        elif inv.family in ("mocha", "vitest", "playwright", "npm-script"):
            if not shutil.which(npx_cmd()[0]):
                inv.unavailable = ("toolchain", "node toolchain (npx) not found")
            elif not (cwd / "node_modules").is_dir():
                inv.unavailable = ("toolchain", f"{inv.cwd}/node_modules missing (run npm ci)")
        if inv.unavailable:
            _mark(not_run, inv.ids, *inv.unavailable)
            continue
        for i in inv.ids:
            unmet = list(dict.fromkeys(n[4:] for n in rows[i]["needs"] if n.startswith("env:") and _unset_env(n[4:])))
            # Meeting the stricter of two overlapping needs meets both, so name only it.
            missing = [_unset_env(n) for n in unmet if not any(set(o.split("|")) < set(n.split("|")) for o in unmet)]
            if missing and os.environ.get("PIPESHUB_REQUIRE_CONNECTOR_SECRETS", "").lower() not in ("1", "true", "yes"):
                _mark(not_run, [i], "secret", "missing env " + ", ".join(missing))
    if stack_mode == "managed" and any(inv.needs_stack and not inv.unavailable for inv in invs):
        if not shutil.which("docker") or not _probe(["docker", "info"], cfg.root):
            fatal.append("docker is not available")
        free_gb = shutil.disk_usage(cfg.root).free / 2**30
        min_gb = float(os.environ.get("QA_MIN_DISK_GB", "20"))
        if free_gb < min_gb:
            fatal.append(f"only {free_gb:.0f} GB free disk, need {min_gb:.0f}")
        busy = [p for p in STACK_PORTS if not _port_free(p)]
        if busy:
            fatal.append(f"ports in use: {', '.join(map(str, busy))} (use --stack external for a running stack)")
    return not_run, fatal


# --- execute and collect ----------------------------------------------------------

class _Cancelled(Exception):
    pass


def _on_term(signum, frame):
    raise _Cancelled()


def _execute(cfg: Config, inv: Invocation, log_dir: Path, extra_env: dict[str, str]) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{inv.label}.log"
    env = {**os.environ, **extra_env, **inv.env}
    timeout = int(os.environ.get(f"QA_TIMEOUT_{inv.family.upper().replace('-', '_')}", DEFAULT_TIMEOUT.get(inv.family, 3600)))
    started = time.monotonic()
    if inv.junit:
        inv.junit.unlink(missing_ok=True)
    with log.open("w", encoding="utf-8") as fh:
        fh.write(f"$ (cd {inv.cwd} && {shlex.join(inv.cmd)})\n")
        fh.flush()
        try:
            code = subprocess.run(inv.cmd, check=False, cwd=cfg.root / inv.cwd, env=env, stdout=fh, stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = -1
            fh.write(f"\nqa: timed out after {timeout}s\n")
        except OSError as exc:
            code = -2
            fh.write(f"\nqa: could not start: {exc}\n")
    return {"label": inv.label, "cwd": inv.cwd, "cmd": shlex.join(inv.cmd), "exit": code, "seconds": round(time.monotonic() - started, 1), "log": str(log)}


def _outcomes(inv: Invocation, rows: dict[str, dict[str, Any]], exit_code: int) -> dict[str, dict[str, Any]]:
    if inv.junit and not inv.junit.is_file():
        # A runner in a container that cannot see the output directory looks like a collection error otherwise.
        detail = f"{inv.label} wrote no JUnit file at {inv.junit} (exit {exit_code}); see its log"
        return {i: {"outcome": "error", "seconds": None, "details": detail} for i in inv.ids}
    cases = junit.read(inv.junit) if inv.junit else []
    results: dict[str, dict[str, Any]] = {}
    if inv.family in ("pytest", "it-pytest"):
        per_file: dict[str, list[junit.Case]] = defaultdict(list)
        per_test: dict[str, list[junit.Case]] = defaultdict(list)
        for c in cases:
            f, name = junit.pytest_key(c, inv.modules)
            if f:
                per_file[f].append(c)
                per_test[f"{f}::{name}"].append(c)
        # Session-level problems (xdist collection mismatch, internal errors) have no file.
        orphan = next((f"{c.name}: {c.message}" for c in cases if c.outcome == "error" and not c.classname and c.name not in inv.modules), "")
        for i in inv.ids:
            rel = i.split(":", 1)[1]
            got = per_test.get(rel) if "::" in rel else per_file.get(rel)
            results[i] = _summarize(got, exit_code)
            if not got and orphan:
                results[i]["details"] = f"run-level error: {orphan[:400]}"
    else:
        for i in inv.ids:
            rel = rel_to(rows[i]["path"], inv.cwd)
            got = [c for c in cases if junit.file_match(c, rel)]
            if got:
                results[i] = _summarize(got, exit_code)
            else:
                outcome = "pass" if exit_code == 0 else ("error" if exit_code < 0 else "fail")
                detail = "" if exit_code == 0 else f"{inv.label} exited {exit_code}; see its log (no per-file result)"
                results[i] = {"outcome": outcome, "seconds": None, "details": detail}
    return results


def _summarize(cases: list[junit.Case] | None, exit_code: int) -> dict[str, Any]:
    if not cases:
        return {"outcome": "error", "seconds": None, "details": f"no result in JUnit (exit {exit_code}); collection error or not collected"}
    outcome = junit.worst([c.outcome for c in cases])
    failing = [f"{c.name}: {c.message}" for c in cases if c.outcome in ("fail", "error")][:3]
    return {"outcome": outcome, "seconds": round(sum(c.seconds for c in cases), 2), "details": " | ".join(failing)[:800]}


def _retry_ids(inv: Invocation, results: dict[str, dict[str, Any]]) -> list[str]:
    if inv.family not in ("pytest", "it-pytest"):
        return []
    return [i for i, r in results.items() if r["outcome"] in ("fail", "error")]


def run(
    cfg: Config, plan: dict[str, Any], rows: dict[str, dict[str, Any]], out: Path,
    stack_mode: str = "none", base_url: str | None = None, build: bool = False,
    dry_run: bool = False, retry: bool = True, scope: str = "all",
    shard: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """scope: "all", "unit" (no stack needed) or "stack" (integration and e2e only).
    shard (k, n): run only stack shard k of n; the other shards' stack tests are not_run scope."""
    out.mkdir(parents=True, exist_ok=True)
    log_dir = out / "logs"
    elsewhere: set[str] = set()
    if shard:
        elsewhere = set(shard_mod.stack_ids(cfg, plan, rows)) - shard_mod.select(cfg, plan, rows, *shard)
    # Resolve without them, so no runner command names another shard's files.
    invs, not_run = resolve(cfg, {**plan, "tests": [t for t in plan["tests"] if t["id"] not in elsewhere]}, rows, out)
    if shard:
        _mark(not_run, sorted(elsewhere), "scope", f"runs in another stack shard (this is {shard[0]}/{shard[1]})")
    if scope != "all":
        keep = [i for i in invs if i.needs_stack == (scope == "stack")]
        kept_ids = {t for i in keep for t in i.ids}
        _mark(not_run, [t["id"] for t in plan["tests"] if t["id"] not in kept_ids], "scope", f"outside --scope {scope}")
        invs = keep
    if dry_run:
        return {"dry_run": True, "invocations": invs, "not_run": not_run, "stack": _stacks(cfg, plan, stack_mode, base_url, log_dir, build, out, shard)}

    started = time.time()
    pre_not_run, fatal = preflight(cfg, invs, rows, stack_mode)
    for k, (kind, why) in pre_not_run.items():
        _mark(not_run, [k], kind, why)
    results: dict[str, dict[str, Any]] = {}
    invocation_log: list[dict[str, Any]] = []
    stack_events: list[dict[str, Any]] = []
    environment: dict[str, Any] = {}
    previous = signal.signal(signal.SIGTERM, _on_term)
    try:
        plain = [i for i in invs if not i.needs_stack and not i.unavailable]
        stacked = [i for i in invs if i.needs_stack and not i.unavailable]
        for inv in plain:
            _run_one(cfg, inv, rows, log_dir, {}, not_run, results, invocation_log, retry)
        if stacked:
            if fatal:
                for inv in stacked:
                    for i in inv.ids:
                        _mark(not_run, [i], "infra", "infra: " + "; ".join(fatal))
            else:
                stacks = _stacks(cfg, plan, stack_mode, base_url, log_dir, build, out, shard)
                setup_error = _prepare(cfg, stacks, out) if stack_mode == "managed" and stacks else None
                if setup_error:
                    for inv in stacked:
                        for i in inv.ids:
                            _merge_outcome(results, i, {"outcome": "error", "seconds": None, "details": setup_error})
                    stack_events += [{"graph_db": s.graph_db, "events": [setup_error]} for s in stacks]
                else:
                    served = {s.graph_db for s in stacks}
                    carried = {i for inv in stacked if inv.graph_db in (None, *served) for i in inv.ids}
                    for inv in stacked:
                        if inv.graph_db not in (None, *served):
                            for i in inv.ids:
                                if i in carried:
                                    continue
                                _mark(not_run, [i], "scope", f"the external stack runs {', '.join(sorted(served))}, not {inv.graph_db}")
                    for g in sorted(set(plan["stack"].get("graph_db", [])) - served):
                        stack_events.append({"graph_db": g, "events": [f"skipped: the external stack runs {', '.join(sorted(served))}"]})
                    try:
                        for stack in stacks:
                            these = [i for i in stacked if i.graph_db in (None, stack.graph_db)]
                            try:
                                stack.start()
                                for inv in these:
                                    _run_one(cfg, inv, rows, log_dir, stack.env(), not_run, results, invocation_log, retry)
                            except (RuntimeError, subprocess.SubprocessError) as exc:
                                for inv in these:
                                    for i in inv.ids:
                                        _merge_outcome(results, i, {"outcome": "error", "seconds": None, "details": f"stack {stack.graph_db}: {exc}"})
                            finally:
                                environment = environment or stack.environment
                                stack.collect_logs()
                                stack.stop()
                                stack_events.append({"graph_db": stack.graph_db, "events": stack.events})
                    finally:
                        if stacks and build and stack_mode == "managed" and os.environ.get("QA_KEEP_IMAGE") != "1":
                            _remove_image(stack_mod.image_tag(plan.get("head")), stacks[-1].events)
    except (_Cancelled, KeyboardInterrupt):
        for t in plan["tests"]:
            if t["id"] not in results:
                _mark(not_run, [t["id"]], "cancelled", "run cancelled")
    finally:
        signal.signal(signal.SIGTERM, previous)

    tests = []
    for t in plan["tests"]:
        tid = t["id"]
        if tid in results:
            tests.append({"id": tid, **results[tid]})
        else:
            kind, why = not_run.get(tid, ("no_result", "not executed"))
            tests.append({"id": tid, "outcome": "not_run", "kind": kind, "seconds": None, "details": why})
    return {
        **({"environment": environment} if environment else {}),
        "schema": "qa-results/v1",
        "scope": scope,
        "head": plan.get("head"),
        "started": started,
        "seconds": round(time.time() - started, 1),
        "stack_mode": stack_mode,
        "fatal": fatal,
        "tests": tests,
        "invocations": invocation_log,
        "stack": stack_events,
        "deferred": plan.get("deferred", []),
    }


def _run_one(cfg, inv, rows, log_dir, extra_env, not_run, results, invocation_log, retry) -> None:
    runnable = [i for i in inv.ids if i not in not_run]
    if not runnable:
        return
    if len(runnable) < len(inv.ids) and inv.family == "it-pytest":
        inv.cmd = [c for c in inv.cmd if not _is_target(c)] + _it_targets(inv, runnable, rows)
        inv.ids = runnable
    record = _execute(cfg, inv, log_dir, extra_env)
    invocation_log.append(record)
    got = _outcomes(inv, rows, record["exit"])
    if retry:
        again = _retry_ids(inv, got)
        if again and len(again) <= 50:
            retry_inv = _retry_invocation(inv, again)
            record2 = _execute(cfg, retry_inv, log_dir, extra_env)
            record2["label"] += " (retry)"
            invocation_log.append(record2)
            second = _outcomes(retry_inv, rows, record2["exit"])
            for i in again:
                if second.get(i, {}).get("outcome") == "pass":
                    got[i] = {**second[i], "outcome": "flaky", "details": "failed, then passed on retry: " + got[i]["details"]}
    for i in runnable:
        _merge_outcome(results, i, got.get(i, {"outcome": "error", "seconds": None, "details": "no result"}))


def _merge_outcome(results: dict[str, dict[str, Any]], test_id: str, new: dict[str, Any]) -> None:
    """A test run on several graph databases keeps its worst result; a tie keeps the first."""
    old = results.get(test_id)
    if old is None or junit.worst([old["outcome"], new["outcome"]]) != old["outcome"]:
        results[test_id] = new


def _is_target(arg: str) -> bool:
    return not arg.startswith("-") and (arg.endswith(".py") or "::" in arg)


def _it_targets(inv: Invocation, ids: list[str], rows) -> list[str]:
    return sorted({i.split(":", 1)[1] if not rows[i].get("ordered") else rel_to(rows[i]["path"], inv.cwd) for i in ids})


def _retry_invocation(inv: Invocation, ids: list[str]) -> Invocation:
    targets = sorted({i.split(":", 1)[1] for i in ids})
    jx = inv.junit.with_name(inv.junit.stem + "-retry.xml") if inv.junit else None
    cmd = [c for c in inv.cmd if not _is_target(c) and not c.startswith("--junitxml=")]
    head = cmd[: cmd.index("pytest") + 1]
    rest = cmd[cmd.index("pytest") + 1:]
    return Invocation(inv.label + "-retry", inv.suite, inv.family, inv.cwd, head + targets + rest + [f"--junitxml={jx}"], ids, jx, env=inv.env, needs_stack=inv.needs_stack, graph_db=inv.graph_db, modules=inv.modules)


def run_id(shard: tuple[int, int] | None) -> str:
    base = os.environ.get("QA_RUN_ID") or os.environ.get("GITHUB_RUN_ID") or str(os.getpid())
    return f"{base}s{shard[0]}" if shard else base


def _prepare(cfg: Config, stacks: list[Stack], out: Path) -> str | None:
    """Overlay hooks and a fresh admin for a managed stack; the error text when a hook fails."""
    try:
        env, files = cfg.prepare_stack(out / "stack")
    except RuntimeError as exc:
        return f"stack setup: {exc}"
    os.environ.update(stack_mod.run_credentials(os.environ))
    for s in stacks:
        s.extra_env.update(env)
        s.overrides += files
    return None


def _remove_image(tag: str, events: list[str]) -> None:
    try:
        subprocess.run(["docker", "image", "rm", tag], check=True, capture_output=True, timeout=300)
        events.append(f"image {tag} removed")
    except (subprocess.SubprocessError, OSError) as exc:
        events.append(f"image removal failed: {exc}")


def _stacks(cfg: Config, plan: dict[str, Any], mode: str, base_url: str | None, log_dir: Path, build: bool, out: Path, shard: tuple[int, int] | None = None) -> list[Stack]:
    if mode == "none" or not plan["stack"].get("needed"):
        return []
    head = plan.get("head")
    rid = run_id(shard)
    url = base_url or os.environ.get("PIPESHUB_BASE_URL", "http://localhost:3000")
    graphs = plan["stack"]["graph_db"]
    if mode == "external":
        # Someone else's stack has one graph DB; running the other pass against it would mislabel the results.
        graphs = [os.environ.get("QA_STACK_GRAPH_DB") or os.environ.get("TEST_GRAPH_DB_TYPE") or graphs[0]]
    stacks = [
        Stack(cfg.root, mode, g, plan["stack"]["broker"], plan["stack"]["compose_profiles"], stack_mod.project_name(head, rid, g), url, log_dir, build and i == 0)
        for i, g in enumerate(graphs)
    ]
    if mode == "managed":
        # Tag the image with the commit so a stale pipeshub-ai:latest from another branch is never reused.
        image = out / "stack" / "image.json"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_text(json.dumps({"services": {stack_mod.APP_SERVICE: {"image": stack_mod.image_tag(head)}}}), encoding="utf-8")
        for s in stacks:
            s.overrides.append(str(image))
            if head:
                s.extra_env["GIT_COMMIT"] = head
    return stacks


def describe_dry_run(dry: dict[str, Any]) -> str:
    lines = ["# qa run --dry-run", ""]
    for stack in dry["stack"]:
        lines += ["## stack", *[f"    {line}" for line in stack.describe()], ""]
    for inv in dry["invocations"]:
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in inv.env.items())
        lines.append(f"## {inv.label}  ({len(inv.ids)} selected)")
        lines.append(f"    cd {inv.cwd} && {env + ' ' if env else ''}{shlex.join(inv.cmd)}")
        lines.append("")
    if dry["not_run"]:
        lines.append("## not run")
        lines += [f"    {i}: {why}" for i, (_, why) in sorted(dry["not_run"].items())]
    return "\n".join(lines) + "\n"
