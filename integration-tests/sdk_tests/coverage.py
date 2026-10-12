#!/usr/bin/env python3
"""List the SDK operations that no SDK test calls.

Usage: coverage.py

Reads the spec and tests.arazzo.yaml next to it, and handwritten/operations.txt.
Prints the operations marked `x-pipeshub-sdk: true` that neither a generated nor
a hand-written test calls. Exits non-zero when a test names an operation the
spec does not have, which means the test list is stale.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

_SDK_TESTS = Path(__file__).resolve().parent
_API_DOCS = _SDK_TESTS.parents[1] / "backend/nodejs/apps/src/modules/api-docs"
_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def sdk_operations(spec: dict) -> dict[str, str]:
    """operationId -> tag, for the operations the SDKs expose."""
    return {
        operation["operationId"]: (operation.get("tags") or ["Untagged"])[0]
        for path_item in spec["paths"].values()
        for method, operation in path_item.items()
        if method in _HTTP_METHODS and operation.get("x-pipeshub-sdk") is True
    }


def generated_operations(arazzo: dict) -> set[str]:
    return {
        step["operationId"]
        for workflow in arazzo["workflows"]
        for step in workflow["steps"]
        if "operationId" in step
    }


def handwritten_operations(text: str) -> set[str]:
    lines = (line.strip() for line in text.splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def main() -> int:
    operations = sdk_operations(
        yaml.safe_load((_API_DOCS / "pipeshub-openapi.yaml").read_text())
    )
    generated = generated_operations(
        yaml.safe_load((_API_DOCS / "tests.arazzo.yaml").read_text())
    )
    handwritten = handwritten_operations(
        (_SDK_TESTS / "handwritten/operations.txt").read_text()
    )

    tested = generated | handwritten
    unknown = sorted(tested - operations.keys())
    untested = sorted(operations.keys() - tested)

    print(f"SDK operations: {len(operations)}")
    print(
        f"Tested: {len(operations) - len(untested)} "
        f"({len(generated & operations.keys())} generated, "
        f"{len(handwritten & operations.keys())} hand-written)"
    )
    print(f"Not tested: {len(untested)}")
    for tag in sorted({operations[name] for name in untested}):
        names = ", ".join(name for name in untested if operations[name] == tag)
        print(f"  {tag}: {names}")
    if unknown:
        print(
            f"Tests name operations the spec does not have: {', '.join(unknown)}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
