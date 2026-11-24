"""Write ``app/plugins_manifest.py`` for an image build.

    python scripts/write_plugins_manifest.py                       # no plug-ins
    python scripts/write_plugins_manifest.py app.plugins.telemetry # the default

Run it after removing the packages of the plug-ins the build leaves out. Uses
only the standard library, so it runs before the dependencies are installed.
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("modules", nargs="*", help="dotted module paths of the plug-ins to load; none writes an empty list")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    args.output.write_text(render(args.modules), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
