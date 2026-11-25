"""Every parser's blocks get ids that follow content (C-1).

Parsers still mint random ids as they build blocks; `BlockIdentity.assign` replaces them once per
parse, whatever the parser. The same bytes parsed twice (a retry, a replica that raced the first
run) must end with the same ids, and different bytes with different ones.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.modules.reconciliation.block_identity import BlockIdentity
from tests.support.parser_registry import DEFERRED, registry

if TYPE_CHECKING:
    from app.models.blocks import BlocksContainer

ORG, VRID = "org-1", "vr-1"
PARSERS = sorted(registry())
PARSER_DIR = Path(__file__).resolve().parents[4] / "app" / "modules" / "parsers"


def _ids(container) -> list[str]:
    return [g.id for g in container.block_groups] + [b.id for b in container.blocks]


async def _parsed(name: str, variant: str, org: str = ORG, vrid: str = VRID) -> BlocksContainer:
    _, sample, build = registry()[name]
    container = await build(sample(variant))
    assert container.blocks or container.block_groups, f"{name} produced nothing"
    assert BlockIdentity.assign(container, org, vrid)
    return container


@pytest.mark.parametrize("parser", PARSERS)
async def test_same_bytes_same_block_ids(parser: str) -> None:
    first, second = await _parsed(parser, "alpha"), await _parsed(parser, "alpha")
    assert _ids(first) == _ids(second)
    ids = _ids(first)
    assert len(set(ids)) == len(ids), "ids within one record must be unique, repeats included"


@pytest.mark.parametrize("parser", PARSERS)
async def test_the_parse_alone_does_not_give_stable_ids(parser: str) -> None:
    """Why the central step exists: without it two parses of the same bytes name blocks differently."""
    _, sample, build = registry()[parser]
    first, second = await build(sample("alpha")), await build(sample("alpha"))
    assert set(_ids(first)).isdisjoint(_ids(second))


@pytest.mark.parametrize("parser", PARSERS)
async def test_changed_content_changes_the_ids_it_touches(parser: str) -> None:
    before, after = await _parsed(parser, "alpha"), await _parsed(parser, "omega")
    assert set(_ids(before)) != set(_ids(after))
    if len(_ids(before)) > 2:
        assert set(_ids(before)) & set(_ids(after)), "content both versions share keeps its ids"


@pytest.mark.parametrize("parser", PARSERS)
async def test_ids_are_scoped_by_org_and_record(parser: str) -> None:
    base = await _parsed(parser, "alpha")
    other_org = await _parsed(parser, "alpha", org="org-2")
    other_record = await _parsed(parser, "alpha", vrid="vr-2")
    assert set(_ids(base)).isdisjoint(_ids(other_org))
    assert set(_ids(base)).isdisjoint(_ids(other_record))


def test_every_parser_module_is_covered_or_deferred_with_a_reason() -> None:
    covered = {module for module, _, _ in registry().values()}
    builders = re.compile(r"BlocksContainer\(|async def parse\(")
    found = {
        path.relative_to(PARSER_DIR).as_posix()
        for path in PARSER_DIR.rglob("*.py")
        if builders.search(path.read_text(encoding="utf-8"))
    }
    assert found - covered - set(DEFERRED) == set(), "a parser is in neither REGISTRY nor DEFERRED"
    assert all(reason.strip() for reason in DEFERRED.values())
    assert set(DEFERRED) - found == set(), "DEFERRED names a file that no longer builds containers"
    assert covered <= found
