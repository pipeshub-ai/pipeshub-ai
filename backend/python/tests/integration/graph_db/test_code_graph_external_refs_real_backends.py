"""Calls into outside libraries on real Neo4j and ArangoDB.

A small repo is written the way production writes it and built by the real edge builder: the blocks' externalRefs
are stored, `find_blocks_by_external_ref_prefix` finds them scoped to the org, connector and repo, and a rebuild
after a call is removed clears the stale ref, whether the backend merged the re-projected block (Neo4j) or
replaced it (ArangoDB).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.modules.code_graph.block_projection import (
    BlockProjectionContext,
    write_code_file_blocks_to_graph,
)
from app.modules.parsers.code_parser import CodeFileParser
from tests.integration.graph_db import test_code_graph_real_backends as base

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

CLIENT = "src/shop/client.py"
FILES = {
    CLIENT: "import requests\nfrom yaml import safe_load\n\n\n"
            "def fetch(url):\n    return requests.get(url)\n\n\n"
            "def parse(text):\n    return safe_load(text)\n\n\n"
            "def mine():\n    return 1\n",
}


@pytest.fixture(params=["neo4j", "arango"])
async def repo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[base._Repo]:
    async with base._connected_repo(request, monkeypatch, FILES) as built:
        yield built


async def _find(repo: base._Repo, prefixes: list[str], **kwargs) -> dict[str, list[str]]:
    rows = await repo.graph.find_blocks_by_external_ref_prefix(
        org_id=kwargs.pop("org_id", repo.org_id), connector_id=kwargs.pop("connector_id", repo.connector_id),
        prefixes=prefixes, limit=kwargs.pop("limit", 100), raise_on_error=True, **kwargs)
    return {row["qualifiedName"]: row["externalRefs"] for row in rows}


async def test_the_build_stores_refs_and_the_prefix_query_finds_them(repo: base._Repo) -> None:
    result = await base._build(repo)
    assert result.external_refs_written == 2
    assert await _find(repo, ["requests:", "requests."]) == {"function:fetch": ["requests:get"]}
    assert await _find(repo, ["yaml:"]) == {"function:parse": ["yaml:safe_load"]}
    assert len(await _find(repo, ["requests:", "yaml:"], limit=1)) == 1
    assert await _find(repo, []) == {}


async def test_the_query_is_scoped_to_the_org_connector_and_repo(repo: base._Repo) -> None:
    await base._build(repo)
    assert await _find(repo, ["requests:"], org_id="org-other") == {}
    assert await _find(repo, ["requests:"], connector_id="conn-other") == {}
    assert await _find(repo, ["requests:"], record_group_ids=["g-other"]) == {}
    assert await _find(repo, ["requests:"], record_group_ids=[repo.group_id]) == {"function:fetch": ["requests:get"]}


async def test_an_unchanged_rebuild_writes_nothing_and_a_removed_call_clears_its_ref(repo: base._Repo) -> None:
    await base._build(repo)
    assert (await base._build(repo)).external_refs_written == 0

    record_id = repo.record_ids[CLIENT]
    source = FILES[CLIENT].replace("return requests.get(url)", "return url")
    container = CodeFileParser().parse_to_blocks(source.encode(), "client.py", CLIENT, "python")
    repo.blobs.records[record_id] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
    await write_code_file_blocks_to_graph(
        graph_provider=repo.graph,
        context=BlockProjectionContext(org_id=repo.org_id, record_id=record_id, record_group_id=repo.group_id,
                                       connector_id=repo.connector_id, language="python", file_path=CLIENT),
        block_containers=container,
    )
    await base._build(repo, touched={record_id})
    assert await _find(repo, ["requests:"]) == {}
    assert await _find(repo, ["yaml:"]) == {"function:parse": ["yaml:safe_load"]}
