"""Records what the legacy parse pool does, so a test can pin it.

``record()`` drives a scripted scenario (two jobs, a worker crash, a third job,
shutdown) and returns the worker launches, the pool's thread names, the result
of each job and the ``parse_pool_workers()`` grid. ``fixtures/parse_pool_off_trace.json``
was produced by running ``python -m tests.unit.modules.parsers.parse_pool_trace``
on the tree before the lane pool existed.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

FIXTURE = Path(__file__).parent / "fixtures" / "parse_pool_off_trace.json"
WORKERS_GRID = (None, "", "0", "1", "3", "99", "x")


def _governor(heavy: int) -> MagicMock:
    governor = MagicMock()
    governor.ceilings = SimpleNamespace(heavy=heavy)
    return governor


def workers_grid(parse_pool) -> dict[str, int | str]:
    out: dict[str, int | str] = {}
    saved = os.environ.get("PARSE_POOL_WORKERS")
    try:
        for value in WORKERS_GRID:
            if value is None:
                os.environ.pop("PARSE_POOL_WORKERS", None)
            else:
                os.environ["PARSE_POOL_WORKERS"] = value
            for heavy in range(1, 65):
                parse_pool.set_resource_governor(_governor(heavy))
                out[f"{value!r}/{heavy}"] = parse_pool.parse_pool_workers()
    finally:
        parse_pool.set_resource_governor(None)
        if saved is None:
            os.environ.pop("PARSE_POOL_WORKERS", None)
        else:
            os.environ["PARSE_POOL_WORKERS"] = saved
    return out


def _normal(value: str) -> str:
    root = str(Path(__file__).resolve().parents[4])
    return value.replace(root, "<root>").replace(sys.executable, "<python>")


async def _scenario(parse_pool) -> list[str]:
    results = []
    results.append(str(type(await parse_pool.submit(os.getpid, label="one")).__name__))
    results.append(str(type(await parse_pool.submit(os.getpid, label="two")).__name__))
    try:
        await parse_pool.submit(os._exit, 3, label="crash")
        results.append("no error")
    except Exception as exc:
        results.append(type(exc).__name__)
    results.append(str(type(await parse_pool.submit(os.getpid, label="three")).__name__))
    return results


def record(parse_pool) -> dict:
    launches: list[dict] = []
    real_popen = subprocess.Popen
    base_env = dict(os.environ)

    class SpyPopen(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw) -> None:
            env = kw.get("env") or {}
            launches.append({
                "argv": [_normal(str(x)) if not str(x).isdigit() else "<fd>" for x in args],
                "pass_fds": len(kw.get("pass_fds", ())),
                "start_new_session": bool(kw.get("start_new_session")),
                "stdin_devnull": kw.get("stdin") == subprocess.DEVNULL,
                "env_added": {
                    k: _normal(v.split(os.pathsep)[0] if k == "PYTHONPATH" else v)
                    for k, v in sorted(env.items())
                    if base_env.get(k) != v
                },
                "env_removed": sorted(set(base_env) - set(env)),
            })
            super().__init__(args, *a, **kw)

    os.environ["PARSE_POOL_WORKERS"] = "2"
    before = {t.name for t in threading.enumerate()}
    parse_pool.set_resource_governor(_governor(8))
    subprocess.Popen = SpyPopen  # type: ignore[misc]
    try:
        results = asyncio.run(_scenario(parse_pool))
        names = sorted(t.name for t in threading.enumerate() if t.name not in before)
    finally:
        subprocess.Popen = real_popen  # type: ignore[misc]
        parse_pool.shutdown_parse_pool()
        parse_pool.set_resource_governor(None)
        os.environ.pop("PARSE_POOL_WORKERS", None)
    unique = [json.loads(x) for x in sorted({json.dumps(item, sort_keys=True) for item in launches})]
    return {
        "launches": unique,
        "launch_count": len(launches),
        "threads": names,
        "results": results,
        "grid": workers_grid(parse_pool),
    }


def module_hashes(path: Path) -> dict[str, str]:
    """A hash of every top-level statement and every method body of a module.

    Keys are stable names (``Class.method``, ``function``, ``assign:NAME``,
    ``import``) so a test can say exactly which entries may differ.
    """
    tree = ast.parse(path.read_text())
    out: dict[str, str] = {}

    def digest(node: ast.AST) -> str:
        return hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()[:16]

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            header = ast.ClassDef(node.name, node.bases, node.keywords, [], node.decorator_list)
            out[f"class {node.name}"] = digest(header)
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{node.name}.{item.name}"] = digest(item)
                elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                    target = item.targets[0] if isinstance(item, ast.Assign) else item.target
                    out[f"{node.name}.{ast.unparse(target)}"] = digest(item)
                else:
                    out[f"{node.name}:{type(item).__name__}"] = digest(item)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = digest(node)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out[f"import:{ast.unparse(node)}"] = digest(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            out[f"assign:{ast.unparse(target)}"] = digest(node)
        elif isinstance(node, ast.If):
            out["if:" + ast.unparse(node.test)] = digest(node)
        else:
            out[f"stmt:{ast.unparse(node)[:40]}"] = digest(node)
    return out


def main() -> None:
    from app.modules.parsers import parse_pool
    from app.modules.parsers.pdf import table_detector

    base = Path(parse_pool.__file__).parent
    payload = record(parse_pool)
    # Hash the committed sources, not the working tree, so the fixture is main's.
    hashes = {}
    for name, module in (("parse_pool.py", parse_pool), ("pdf/table_detector.py", table_detector)):
        rel = Path(module.__file__).resolve().relative_to(Path(__file__).resolve().parents[4]).as_posix()
        blob = subprocess.run(["git", "show", f"HEAD:backend/python/{rel}"], capture_output=True, text=True, check=True).stdout
        tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"hash_{name.replace('/', '_')}"
        tmp.write_text(blob)
        hashes[name] = module_hashes(tmp)
        tmp.unlink()
    payload["recorded_from"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    payload["hashes"] = hashes
    FIXTURE.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"wrote {FIXTURE} from {base}")


if __name__ == "__main__":
    main()
