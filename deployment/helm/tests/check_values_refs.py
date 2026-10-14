"""Fail when a template reads a .Values path that values.yaml does not define.

A missing key renders as an empty string rather than an error. That is how the
config block once ended up nested under `sandbox:`: PORT rendered empty, the
Node server fell back to 3000 while every probe targeted 3001, and the chart
installed but never became healthy.

Direct reads (`.Values.a.b`, `$.Values.a.b`) are checked, and so are member
reads through a variable bound to a values path in the same file
(`$external := .Values.redis.external` ... `$external.enabled`).

    python3 deployment/helm/tests/check_values_refs.py [chart_dir]
"""

import re
import sys
from pathlib import Path

import yaml

# Settable but deliberately absent from values.yaml: each appears there only as
# a commented-out example, and the templates guard it with `if`/`default`/`required`.
OPTIONAL = {
    "autoscaling.behavior",
    "mongodb.auth.rootPassword",
    "podDisruptionBudget.maxUnavailable",
    "redis.auth.password",
}

_PATH = r"((?:\.[A-Za-z_][A-Za-z0-9_]*)+)"
DIRECT = re.compile(r"\.Values" + _PATH)
ALIAS = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\s*:?=\s*\$?\.Values" + _PATH)


def template_refs(text: str) -> set[str]:
    """Every values path a template reads, directly or through an alias."""
    refs = {m.lstrip(".") for m in DIRECT.findall(text)}
    for name, base in ALIAS.findall(text):
        member = re.compile(r"\$" + re.escape(name) + r"\b" + _PATH)
        refs.update(base.lstrip(".") + m for m in member.findall(text))
    return refs


def missing_refs(chart: Path) -> list[str]:
    values = yaml.safe_load((chart / "values.yaml").read_text(encoding="utf-8"))
    refs: set[str] = set()
    for template in (chart / "templates").iterdir():
        refs |= template_refs(template.read_text(encoding="utf-8"))

    missing = []
    for ref in sorted(refs):
        if ref in OPTIONAL:
            continue
        node = values
        for key in ref.split("."):
            if not isinstance(node, dict) or key not in node:
                missing.append(ref)
                break
            node = node[key]
    return missing


def main() -> int:
    chart = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "pipeshub-ai"
    missing = missing_refs(chart)
    if missing:
        print("templates read values that values.yaml does not define (they render empty):")
        for ref in missing:
            print(f"  .Values.{ref}")
        return 1
    print("every .Values path the templates read is defined in values.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
