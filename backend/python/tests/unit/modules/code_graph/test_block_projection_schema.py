"""Projected block documents satisfy the ArangoDB `blocks` schema.

Arango enforces `block_schema` server-side; Neo4j does not (existence
constraints need Enterprise, and its app-side validation drops `required`).
`serialize_block_for_graph` is the one place a block becomes a graph document,
so both backends get the same shape only if that function holds it. This test
keeps the serializer and the Arango schema from drifting apart.
"""
from typing import Any

import jsonschema
import pytest

from app.models.blocks import BlocksContainer
from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    serialize_block_for_graph,
    write_code_file_blocks_to_graph,
)
from app.modules.parsers.code_parser.code_file_parser import CodeFileParser
from app.schema.arango.documents import block_schema

_VALIDATOR = jsonschema.validators.validator_for(block_schema["rule"])(block_schema["rule"])

_PYTHON = b'''import os
from .base import Base


class Client(Base):
    timeout: int = 5

    def fetch(self, path: str) -> str:
        def _join(a, b):
            return os.path.join(a, b)
        return self.get(_join("/api", path))


def main() -> None:
    Client().fetch("x")
'''

_TYPESCRIPT = b'''import { Base } from "./base";

export class Panel extends Base {
  render(): string {
    return this.format(helper(1));
  }
}

export function helper(n: number): number {
  return n + 1;
}
'''


class _CapturingProvider:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    async def get_nodes_by_filters(self, *args: object, **kwargs: object) -> list:
        return []

    async def delete_nodes(self, *args: object, **kwargs: object) -> bool:
        return True

    async def batch_upsert_nodes(self, docs: list[dict[str, Any]], collection: str,
                                 transaction: str | None = None) -> bool:
        self.docs.extend(docs)
        return True

    async def batch_upsert_record_relations(self, edges: list, transaction: str | None = None) -> bool:
        return True

    async def delete_edges_touching_nodes(self, *args: object, **kwargs: object) -> int:
        return 0


def _context(org_id: str = "org-1", record_id: str = "rec-1") -> BlockProjectionContext:
    return BlockProjectionContext(
        org_id=org_id, record_id=record_id, record_group_id="repo-1",
        connector_id="conn-1", language=None,
    )


async def _project(source: bytes, name: str, language: str) -> list[dict[str, Any]]:
    containers = CodeFileParser().parse_to_blocks(source, name, f"src/{name}", language)
    assert containers is not None
    provider = _CapturingProvider()
    await write_code_file_blocks_to_graph(
        graph_provider=provider, context=_context(), block_containers=containers,
    )
    return provider.docs


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source", "name", "language"),
    [(_PYTHON, "client.py", "python"), (_TYPESCRIPT, "panel.ts", "typescript")],
    ids=["python", "typescript"],
)
async def test_every_projected_block_matches_the_arango_schema(
    source: bytes, name: str, language: str,
) -> None:
    docs = await _project(source, name, language)
    assert len(docs) > 3, "the fixture must exercise groups, methods and nested defs"
    assert any(d.get("pendingEdges") for d in docs), "and carry pending edges"
    for doc in docs:
        errors = sorted(_VALIDATOR.iter_errors({k: v for k, v in doc.items() if k != "id"}),
                        key=lambda e: list(e.path))
        assert not errors, f"{doc.get('qualifiedName')}: {[e.message for e in errors]}"


def test_the_validator_is_live() -> None:
    """Guards the test above against a schema that accepts anything."""
    assert list(_VALIDATOR.iter_errors({"recordId": "rec-1", "startLine": "12"}))


@pytest.mark.parametrize(
    ("org_id", "record_id", "missing"),
    [("", "rec-1", "orgId"), ("org-1", "", "recordId")],
)
def test_a_block_without_org_or_record_is_refused(org_id: str, record_id: str, missing: str) -> None:
    containers = CodeFileParser().parse_to_blocks(_PYTHON, "client.py", "src/client.py", "python")
    assert isinstance(containers, BlocksContainer)
    with pytest.raises(ValueError, match=missing):
        serialize_block_for_graph(
            containers.blocks[0], _context(org_id=org_id, record_id=record_id),
            block_id="b1", is_block_group=False, parent_block_id=None,
        )
