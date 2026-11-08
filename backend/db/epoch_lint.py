"""CI lint for revision ids: ``python -m db.epoch_lint [module_dir ...]`` (default: every module location).

Per module directory (the directory name is the branch): ids are ``<branch>_<NNNN>`` and the file name
starts with ``NNNN_``; NNNN is the parent's NNNN + 1 and the base is 0001; no duplicate ids; exactly one
head. Alembic alone would happily apply two heads, so a fork or a duplicate number from two pull
requests is caught here, before merge.
"""
import ast
import re
import sys
from pathlib import Path

from db.locations import version_locations


def lint_branch(bdir: Path) -> list[str]:
    bad, revs = [], {}
    for f in sorted(bdir.glob("*.py")):
        vals = {}
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id in ("revision", "down_revision"):
                        vals[t.id] = ast.literal_eval(node.value)
        rid, down = vals.get("revision"), vals.get("down_revision")
        m = re.fullmatch(rf"{bdir.name}_(\d{{4}})", rid or "")
        if not m:
            bad.append(f"{f.name}: revision {rid!r} is not {bdir.name}_NNNN")
            continue
        if not f.name.startswith(m[1] + "_"):
            bad.append(f"{f.name}: file prefix does not match {rid}")
        if rid in revs:
            bad.append(f"{f.name}: duplicate revision id {rid} (also {revs[rid][1]})")
        revs[rid] = (down, f.name)
        want = None if m[1] == "0001" else f"{bdir.name}_{int(m[1]) - 1:04d}"
        if down != want:
            bad.append(f"{f.name}: down_revision {down!r}, expected {want!r} (epoch must be parent + 1)")
    parents = {d for d, _ in revs.values() if d}
    heads = sorted(r for r in revs if r not in parents)
    if len(heads) != 1:
        bad.append(f"branch {bdir.name}: {len(heads)} heads {heads}")
    return bad


def main(argv: list[str]) -> int:
    dirs = [Path(a) for a in argv] or [Path(p) for p in version_locations()]
    bad = [msg for d in dirs for msg in lint_branch(d)]
    print("\n".join(bad) or "epoch lint: OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
