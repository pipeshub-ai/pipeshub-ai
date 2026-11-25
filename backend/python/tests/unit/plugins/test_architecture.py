"""Application code never imports, names or calls telemetry: it announces platform events.

Only the telemetry plug-in package and the plug-in manifest may. A build that leaves
telemetry out deletes those two and must find nothing else to fix. A build with
more plug-ins names their packages in ``tests/support/edition_defaults.py``; each
is one a build may delete, so each may hold a client library.
"""

import ast
from pathlib import Path

from tests.support import edition_defaults

APP = Path(__file__).resolve().parents[3] / "app"
ALLOWED = ("plugins_manifest.py", *(package.removeprefix("app/") + "/" for package in edition_defaults.optional_plugin_packages()))


def _modules() -> list[Path]:
    return [p for p in sorted(APP.rglob("*.py")) if not p.relative_to(APP).as_posix().startswith(ALLOWED)]


def _violations(path: Path, root: Path = APP) -> list[str]:
    source = path.read_text(encoding="utf-8-sig")
    where = path.relative_to(root.parent).as_posix()
    found = []
    if "prometheus_client" in source:
        found.append(f"{where}: references prometheus_client")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [node.module or ""] + [f"{node.module or ''}.{alias.name}" for alias in node.names]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and "plugins.telemetry" in node.value:
            imported = [node.value]  # importlib.import_module("app.plugins.telemetry...")
        else:
            continue
        found += [f"{where}:{node.lineno}: imports {name}" for name in imported if _is_telemetry_module(name)]
    return found


def _is_telemetry_module(dotted: str) -> bool:
    # The module path, not the imported name: ``disable_vendor_telemetry`` in
    # ``app.utils.process_hardening`` switches a third party's collection off.
    return any("telemetry" in part.lower() for part in dotted.split(".")[:-1]) or dotted.lower().endswith(".telemetry") or dotted.lower() == "telemetry"


def test_the_walk_sees_the_application() -> None:
    assert len(_modules()) > 1000


def test_no_module_outside_the_plugin_imports_telemetry_or_the_prometheus_client() -> None:
    violations = [v for path in _modules() for v in _violations(path)]
    assert violations == []


def test_the_check_catches_each_kind_of_import(tmp_path: Path) -> None:
    bad = tmp_path / "app" / "bad.py"
    bad.parent.mkdir()
    for source in (
        "from app.plugins.telemetry.backend import METRICS_BACKEND\n",
        "from app.plugins.telemetry.modules import web_metrics\n",
        "from app.plugins import telemetry\n",
        "import app.plugins.telemetry.flag\n",
        "import importlib\nimportlib.import_module('app.plugins.telemetry')\n",
        "import prometheus_client\n",
        "from prometheus_client import Counter\n",
        "from app import telemetry\n",
    ):
        bad.write_text(source)
        assert _violations(bad, bad.parent), source
    bad.write_text("from app.utils.process_hardening import disable_vendor_telemetry\nfrom app import platform_events\n")
    assert _violations(bad, bad.parent) == []
