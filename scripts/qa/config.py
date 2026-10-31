"""Load the rules: the base files first, then every overlay the checkout has."""

from __future__ import annotations

import datetime as dt
import importlib.util
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from qa import paths


@dataclass
class Config:
    root: Path
    engine_root: Path
    suites: list[dict[str, Any]] = field(default_factory=list)
    rules: list[dict[str, Any]] = field(default_factory=list)
    compose_profiles: dict[str, list[str]] = field(default_factory=dict)
    always: dict[str, Any] = field(default_factory=dict)
    quarantine: list[dict[str, Any]] = field(default_factory=list)
    modules: list[dict[str, Any]] = field(default_factory=list)
    flows: list[dict[str, Any]] = field(default_factory=list)
    overlays: list[Path] = field(default_factory=list)
    hooks: list[ModuleType] = field(default_factory=list)

    def edition(self, path: str) -> str:
        for hook in self.hooks:
            ed = hook.edition(path)
            if ed is not None:
                return ed
        return paths.BASE_EDITION

    def editions(self) -> list[str]:
        return ["all", paths.BASE_EDITION, *sorted({s["edition"] for s in self.suites if "edition" in s} - {paths.BASE_EDITION})]

    def suite(self, suite_id: str) -> dict[str, Any] | None:
        return next((s for s in self.suites if s["id"] == suite_id), None)

    def flow(self, flow_id: str) -> dict[str, Any] | None:
        return next((f for f in self.flows if f["id"] == flow_id), None)

    def profile(self, name: str) -> dict[str, Any]:
        profiles = self.always.get("profiles", {})
        if name not in profiles:
            raise KeyError(f"unknown profile {name!r}; known: {sorted(profiles)}")
        return profiles[name]

    def quarantined_ids(self, today: dt.date | None = None) -> set[str]:
        today = today or dt.date.today()
        return {q["id"] for q in self.quarantine if _as_date(q.get("expires")) >= today}


def _as_date(value: Any) -> dt.date:
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        return dt.date.fromisoformat(value)
    return dt.date.min


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def _merge_always(base: dict[str, Any], extra: dict[str, Any]) -> None:
    for key, value in extra.items():
        if isinstance(value, list):
            base.setdefault(key, []).extend(value)
        elif isinstance(value, dict):
            base.setdefault(key, {}).update(value)
        else:
            base[key] = value


LIST_KEYS = ("paths", "modules", "triggers", "tests", "markers")


def merge_by_id(base: list[dict[str, Any]], extra: list[dict[str, Any]], kind: str) -> None:
    """Add entries from extra to base; a repeated id may only extend list keys."""
    ids = [e["id"] for e in extra]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ValueError(f"duplicate {kind} ids: {dupes}")
    for entry in extra:
        match = next((b for b in base if b["id"] == entry["id"]), None)
        if match is None:
            base.append(dict(entry))
            continue
        for key, value in entry.items():
            if key == "id":
                continue
            if key not in LIST_KEYS:
                raise ValueError(f"{kind} {entry['id']}: an overlay may only extend {', '.join(LIST_KEYS)}, not {key}")
            match[key] = [*match.get(key, []), *value]


def _substitute(value: Any, name: str) -> Any:
    if isinstance(value, str):
        return value.replace("{name}", name)
    if isinstance(value, list):
        return [_substitute(v, name) for v in value]
    return value


def expand_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rule in rules:
        names = rule.get("names")
        if not names:
            out.append(rule)
            continue
        for name in names:
            out.append({k: _substitute(v, name) for k, v in rule.items() if k != "names"})
    return out


def _load_hook(overlay: Path) -> ModuleType | None:
    """An overlay's optional qa_hooks.py, which classifies paths into its editions."""
    file = overlay / "qa_hooks.py"
    if not file.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"qa_hooks_{overlay.parent.name}", file)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as err:
        raise RuntimeError(f"{file}: {err}") from err
    return module


def load(root: Path | None = None, engine_root: Path | None = None) -> Config:
    """Catalog, tests and changes come from root; rules and quarantine from engine_root."""
    engine_root = (engine_root or root or paths.REPO).resolve()
    root = (root or paths.tree_root()).resolve()
    cfg = Config(root=root, engine_root=engine_root)
    dirs = [engine_root / paths.RULES_DIR]
    cfg.overlays = paths.overlays(engine_root)
    dirs += [o / "rules" for o in cfg.overlays]
    cfg.hooks = [h for h in (_load_hook(o) for o in cfg.overlays) if h]
    for d in dirs:
        suites = _read(d / "suites.toml")
        cfg.suites.extend(suites.get("suite", []))
        cfg.compose_profiles.update(suites.get("compose_profiles", {}))
        cfg.rules.extend(expand_rules(_read(d / "impact.toml").get("rule", [])))
        _merge_always(cfg.always, _read(d / "always.toml"))
        merge_by_id(cfg.modules, _read(d / "modules.toml").get("module", []), "module")
        merge_by_id(cfg.flows, _read(d / "flows.toml").get("flow", []), "flow")
        cfg.quarantine.extend(_read(d.parent / "quarantine.toml").get("test", []))
    ids = [s["id"] for s in cfg.suites]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate suite ids: {sorted(dupes)}")
    return cfg


_FILES_CACHE: dict[Path, list[str]] = {}


def list_files(root: Path) -> list[str]:
    """Tracked plus untracked-but-not-ignored files, relative to root."""
    root = root.resolve()
    if root not in _FILES_CACHE:
        try:
            out = subprocess.run(
                ["git", "ls-files", "-co", "--exclude-standard"],
                cwd=root, check=True, capture_output=True, text=True,
            ).stdout
            files = [line for line in out.splitlines() if line and (root / line).is_file()]
        except (subprocess.CalledProcessError, FileNotFoundError):
            files = [
                str(p.relative_to(root)) for p in root.rglob("*")
                if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts
            ]
        _FILES_CACHE[root] = sorted(files)
    return _FILES_CACHE[root]


def clear_cache() -> None:
    _FILES_CACHE.clear()
