"""Shared helpers for the harness contract tests (P0.5, TEST-STRATEGY 3.4)."""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from jsonschema import Draft202012Validator
from pydantic import BaseModel
from referencing import Registry, Resource

if TYPE_CHECKING:
    from types import ModuleType

PYTHON_ROOT = Path(__file__).resolve().parents[3]
REPO = PYTHON_ROOT.parents[1]
CONTRACTS_ROOT = REPO / "contracts"
CONTRACTS = CONTRACTS_ROOT / "harness"
CONTROL_PLANE = CONTRACTS_ROOT / "control-plane"
NODE_APP = REPO / "backend" / "nodejs" / "apps"
NODE_CLI = NODE_APP / "tests" / "support" / "harness" / "contracts-cli.ts"
VECTORS = CONTRACTS / "vectors" / "canonical-json.json"


@cache
def generator() -> ModuleType:
    """``contracts/harness/codegen/generate.py``: its groups are the one list of contract roots."""
    spec = importlib.util.spec_from_file_location("contracts_codegen_generate", CONTRACTS / "codegen" / "generate.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


GENERATOR = generator()
GROUPS = GENERATOR.GROUPS


@dataclass(frozen=True)
class ContractSchema:
    path: Path
    schema: dict[str, Any] = field(hash=False)

    @property
    def rel(self) -> str:
        """Relative to ``contracts/``: ``harness/core/envelope.schema.json``, ``control-plane/problem.schema.json``."""
        return self.path.relative_to(CONTRACTS_ROOT).as_posix()

    @property
    def examples(self) -> list[Any]:
        return list(self.schema.get("examples", []))

    @property
    def invalid(self) -> list[dict[str, Any]]:
        path = self.path.with_name(self.path.name.replace(".schema.json", ".invalid.json"))
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    @property
    def is_event(self) -> bool:
        return "x-writer" in self.schema

    def model(self) -> type[BaseModel]:
        for group in GROUPS:
            if self.path.is_relative_to(group.source):
                parts = self.path.relative_to(group.source).as_posix().removesuffix(".schema.json").split("/")
                stem = parts[-1].replace(".", "_").replace("-", "_")
                module = importlib.import_module(".".join([group.python_package, *parts[:-1], stem]))
                model = getattr(module, self.schema["title"])
                assert isinstance(model, type) and issubclass(model, BaseModel)
                return model
        raise AssertionError(f"no generated package for {self.rel}")

    def __str__(self) -> str:
        return self.rel


def all_schema_files() -> list[Path]:
    return [path for group in GROUPS for path in group.schemas()]


def load_schema(path: Path) -> dict[str, Any]:
    """A schema as the generated models read it: with what an edition's overlay adds (``codegen/extensions.py``)."""
    loaded: dict[str, Any] = GENERATOR.schema_doc(path)
    return loaded


@cache
def instance_schemas() -> tuple[ContractSchema, ...]:
    """Every schema that describes instances (common.schema.json only holds $defs)."""
    out = []
    for path in all_schema_files():
        schema = load_schema(path)
        if "examples" in schema:
            out.append(ContractSchema(path, schema))
    return tuple(out)


@cache
def registry() -> Registry:
    resources = []
    # An extended schema may name an edition overlay's schema (``contracts/harness/ee/``) by its $id.
    for path in [p for group in GENERATOR.all_groups() for p in group.schemas()]:
        schema = load_schema(path)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


def validator(schema: ContractSchema) -> Draft202012Validator:
    return Draft202012Validator(schema.schema, registry=registry())


def bundle(schema: ContractSchema) -> dict[str, Any]:
    """Inline every $ref so tools that only resolve local refs (hypothesis-jsonschema) can use it."""
    reg = registry()

    def walk(node: object, base: str) -> object:
        if isinstance(node, dict):
            if "$ref" in node:
                resolved = reg.resolver(base_uri=base).lookup(node["$ref"])
                target_base = urljoin(base, node["$ref"]).partition("#")[0]
                siblings = {k: v for k, v in node.items() if k != "$ref"}
                return walk({**resolved.contents, **siblings}, target_base)
            return {k: walk(v, base) for k, v in node.items() if k not in ("$defs", "$id", "examples", "$schema")}
        if isinstance(node, list):
            return [walk(v, base) for v in node]
        return node

    bundled = walk(schema.schema, schema.schema["$id"])
    assert isinstance(bundled, dict)
    return bundled


def node_available() -> str | None:
    """Reason the Node side cannot run, or None."""
    if shutil.which("node") is None:
        return "node is not installed"
    for dep in ("ts-node", "zod"):
        if not (NODE_APP / "node_modules" / dep).exists():
            return f"backend/nodejs/apps/node_modules/{dep} missing (run npm ci)"
    return None


def run_node(op: str, items: list[Any]) -> list[dict[str, Any]]:
    env = {
        **os.environ,
        "TS_NODE_PROJECT": str(NODE_APP / "tsconfig.test.json"),
        "TS_NODE_TRANSPILE_ONLY": "true",
    }
    proc = subprocess.run(
        ["node", "-r", "ts-node/register", str(NODE_CLI)],
        input=json.dumps({"op": op, "items": items}),
        capture_output=True,
        text=True,
        cwd=NODE_APP,
        env=env,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr[-4000:]
    results = json.loads(proc.stdout)
    assert len(results) == len(items)
    return results
