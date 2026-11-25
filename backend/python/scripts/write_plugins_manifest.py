"""Write ``app/plugins_manifest.py`` for an image build.

    python scripts/write_plugins_manifest.py                       # no plug-ins
    python scripts/write_plugins_manifest.py app.plugins.telemetry # the default

Run it after removing the packages of the plug-ins the build leaves out. Uses
only the standard library, so it runs before the dependencies are installed.

A plug-in's own libraries are an extra of this project named after the last part
of its module path (``app.plugins.telemetry`` -> ``telemetry``), which the
project's dependencies include by naming itself (``"ai-service[telemetry]"``).
With ``--pyproject`` the script also removes those lines for the plug-ins that
are not listed, so the install that follows leaves their libraries out:

    python scripts/write_plugins_manifest.py --pyproject pyproject.toml
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "app" / "plugins_manifest.py"
_MODULE_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


def render(modules: list[str]) -> str:
    for module in modules:
        if not _MODULE_PATH.fullmatch(module):
            raise ValueError(f"not a dotted module path: {module!r}")
    lines = ['"""The plug-ins this build loads: dotted module paths, each exposing ``plugin``.', "",
             "Written by ``scripts/write_plugins_manifest.py``.", '"""', ""]
    if modules:
        lines += ["PLUGINS: list[str] = [", *(f'    "{module}",' for module in modules), "]"]
    else:
        lines += ["PLUGINS: list[str] = []"]
    return "\n".join(lines) + "\n"


def _project_name(text: str) -> str:
    table = re.search(r"(?ms)^\[project\]\s*$(.*?)(?=^\[)", text + "\n[")
    name = re.search(r'(?m)^name\s*=\s*"([^"]+)"', table.group(1)) if table else None
    if name is None:
        raise ValueError("no [project] name in the pyproject file")
    return name.group(1)


def drop_plugin_extras(text: str, modules: list[str]) -> tuple[str, list[str]]:
    """``text`` without the dependency lines that pull in the extra of a plug-in not in ``modules``.

    Only a line holding nothing but the project's own name with one extra is a plug-in's; returns the new text and
    the extras dropped. The extras stay declared, so the same file still installs them when asked for by name.
    """
    kept = {module.rsplit(".", 1)[-1] for module in modules}
    line = re.compile(rf'^[ \t]*"{re.escape(_project_name(text))}\[([A-Za-z0-9_.-]+)\]",?[ \t]*(#.*)?\r?\n', re.MULTILINE)
    dropped: list[str] = []

    def replace(match: re.Match[str]) -> str:
        if match.group(1) in kept:
            return match.group(0)
        dropped.append(match.group(1))
        return ""

    return line.sub(replace, text), dropped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("modules", nargs="*", help="dotted module paths of the plug-ins to load; none writes an empty list")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--pyproject", type=Path, help="also remove from this pyproject.toml the extras of plug-ins not listed")
    args = parser.parse_args(argv)
    args.output.write_text(render(args.modules), encoding="utf-8")
    if args.pyproject is not None:
        text, _ = drop_plugin_extras(args.pyproject.read_text(encoding="utf-8"), args.modules)
        args.pyproject.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
