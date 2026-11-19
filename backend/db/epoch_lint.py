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


def revision_attrs(f: Path) -> dict:
    vals = {}
    for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("revision", "down_revision", "depends_on", "min_code_epoch"):
                    try:
                        vals[t.id] = ast.literal_eval(node.value)
                    except ValueError:
                        if t.id in ("revision", "down_revision"):
                            raise
                        # the id lint never read these: a computed one is skipped here, not a crash of the lint
                        vals.setdefault("computed", []).append(t.id)
    return vals


def lint_branch(bdir: Path) -> list[str]:
    bad, revs = [], {}
    for f in sorted(bdir.glob("*.py")):
        vals = revision_attrs(f)
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


def _refs(value: str | list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    return () if value is None else (value,) if isinstance(value, str) else tuple(value)


def expected_heads(dirs: list[Path]) -> tuple[dict[str, tuple[int, int]], set[str]]:
    """What migrating to heads must leave, read from the revision files: per branch (directory) ``(epoch, min_code_epoch)``,
    the epoch from the head file's ``NNNN_`` prefix and min_code_epoch the highest any revision declares (``kernel.set_epoch``
    keeps the greatest); and the revisions ``kernel.alembic_version`` keeps, the ones no revision names as its down_revision or
    in depends_on. Raises on a branch with other than one head, or a head whose id is not its file's epoch."""
    epochs, attrs = {}, []
    for d in map(Path, dirs):
        files = {f: revision_attrs(f) for f in sorted(d.glob("*.py"))}
        for f, a in files.items():
            if a.get("computed"):
                # a value only the migration run knows: guessing it would expect less than the database records
                raise ValueError(f"{d.name}/{f.name}: {', '.join(a['computed'])} must be a literal for the expected heads")
        parents = {p for a in files.values() for p in _refs(a.get("down_revision"))}
        tips = [f for f, a in files.items() if a.get("revision") not in parents]
        if len(tips) != 1:
            raise ValueError(f"branch {d.name}: {len(tips)} heads {[f.name for f in tips]}")
        n = int(tips[0].name[:4])
        if files[tips[0]].get("revision") != f"{d.name}_{n:04d}":
            raise ValueError(f"{d.name}/{tips[0].name}: revision {files[tips[0]].get('revision')!r} is not epoch {n} of its file")
        epochs[d.name] = (n, max(a.get("min_code_epoch", 0) for a in files.values()))
        attrs += files.values()
    named = {r for a in attrs for r in (*_refs(a.get("down_revision")), *_refs(a.get("depends_on")))}
    return epochs, {a["revision"] for a in attrs} - named


def main(argv: list[str]) -> int:
    dirs = [Path(a) for a in argv] or [Path(p) for p in version_locations()]
    bad = [msg for d in dirs for msg in lint_branch(d)]
    print("\n".join(bad) or "epoch lint: OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
