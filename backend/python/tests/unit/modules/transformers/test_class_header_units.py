"""Class header units: a class becomes findable as one unit, beside its members.

Every top-level class is a CODE_CLASS group, and code groups were never embedded, so a search for a
class found its methods at best. A class header unit is the class's signature, docstring and member
signatures. Existing repos gain these units through reconciliation: only the group's hash changes,
so the class is re-indexed while every member keeps its vector.
"""
import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import BlocksContainer, GroupSubType
from app.modules.parsers.code_parser import CodeFileParser
from app.modules.reconciliation.service import ReconciliationService
from app.modules.transformers.code_units import UNIT_TOKENS
from app.modules.transformers.transformer import ReconciliationContext
from app.modules.transformers.vectorstore import (
    _build_code_documents,
    _token_len,
    code_documents_for_record,
)
from tests.unit.modules.transformers.test_vectorstore_deep import _make_vectorstore

SRC = b'''
class Client:
    """Talks to the API."""

    def fetchRecord(self, record_id):
        """Fetch one record."""
        return self.http.get(record_id)

    def close(self):
        self.http.close()


class Retry:
    def wait(self, attempt):
        return 2 ** attempt


def helper():
    return Client()
'''


def _parse(src: bytes = SRC) -> BlocksContainer:
    return CodeFileParser().parse_to_blocks(src, "client.py", "src/client.py", "python")


def _classes(container: BlocksContainer) -> list:
    return [g for g in container.block_groups if g.sub_type == GroupSubType.CODE_CLASS]


def _code_blocks(container: BlocksContainer) -> list:
    return [b for b in container.blocks if str(b.type.value) == "code"]


def _record(container: BlocksContainer) -> MagicMock:
    return MagicMock(id="rec-1", virtual_record_id="vr-1", org_id="org-1", semantic_metadata=None,
                     record_name="client.py", external_record_id="src/client.py", block_containers=container)


def _store() -> MagicMock:
    vs = _make_vectorstore()
    vs.get_embedding_model_instance = AsyncMock(return_value=False)
    vs._process_document_chunks = AsyncMock()
    vs.delete_blocks_by_ids = AsyncMock()
    return vs


def _embedded(vs) -> list:
    return [d for call in vs._process_document_chunks.call_args_list for d in call.args[0]]


@pytest.mark.asyncio
async def test_a_reconcile_after_the_units_version_reindexes_only_classes_and_embeds_their_headers() -> None:
    """The path existing repos take: the group hash folds EMBED_UNITS_VERSION, so the reconciliation
    diff re-indexes each class (and drops its old points) while every block keeps its vector."""
    old, new = _parse(), _parse()
    for group in old.block_groups:  # what a class was hashed as before class units existed
        group.content_hash = hashlib.sha256(group.data["text"].encode()).hexdigest()
    service = ReconciliationService()
    to_index, to_delete, unchanged = service.compute_diff(service.build_metadata(old), service.build_metadata(new))

    assert to_index == {g.id for g in new.block_groups}
    assert to_delete == {g.id for g in old.block_groups}
    assert set(unchanged) == {b.id for b in new.blocks}

    service.apply_preserved_ids(new, unchanged)
    vs = _store()
    await vs.apply(MagicMock(record=_record(new), reconciliation_context=ReconciliationContext(
        blocks_to_index_ids=to_index, block_ids_to_delete=to_delete)))

    docs = [d for d in _embedded(vs) if d.metadata["unitKind"] != "residue"]  # the residue is rebuilt too
    assert sorted(d.metadata["blockId"] for d in docs) == sorted(g.id for g in _classes(new))
    assert all(d.metadata["unitKind"] == "class" for d in docs)
    client = next(d for d in docs if "class Client" in d.page_content)
    # The members are not re-indexed, yet the header lists them: they come from the whole record.
    assert "def fetchRecord(self, record_id):" in client.page_content
    assert "def close(self):" in client.page_content


@pytest.mark.asyncio
async def test_a_full_index_embeds_block_units_and_one_header_unit_per_class() -> None:
    container = _parse()
    vs = _store()
    await vs.index_documents(container, "org-1", "rec-1", "vr-1", record=_record(container))

    docs = _embedded(vs)
    classes = [d for d in docs if d.metadata.get("unitKind") == "class"]
    assert sorted(d.metadata["blockId"] for d in classes) == sorted(g.id for g in _classes(container))
    block_units = _build_code_documents(_code_blocks(container), "vr-1", "org-1", "client.py — src/client.py")
    assert [d.page_content for d in docs if d.metadata.get("unitKind") is None] == \
        [d.page_content for d in block_units]


def test_block_units_are_byte_identical_so_cached_embeddings_stay_valid() -> None:
    container = _parse()
    before = _build_code_documents(_code_blocks(container), "vr-1", "org-1", "client.py — src/client.py")
    after = code_documents_for_record(_code_blocks(container), container.block_groups, "vr-1", "org-1",
                                      "client.py — src/client.py")
    blocks_only = [d for d in after if d.metadata.get("unitKind") is None]
    assert [(d.page_content, d.metadata) for d in blocks_only] == [(d.page_content, d.metadata) for d in before]


def test_a_header_unit_is_the_signature_docstring_and_member_signatures_without_bodies() -> None:
    container = _parse()
    docs = code_documents_for_record(_code_blocks(container), container.block_groups, "vr-1", "org-1",
                                     "client.py — src/client.py")
    client_group = next(g for g in _classes(container) if g.name == "Client")
    unit = next(d for d in docs if d.metadata.get("unitKind") == "class" and d.metadata["blockId"] == client_group.id)
    text = unit.page_content
    assert text.split("\n")[0] == "client.py — src/client.py · class:Client"
    assert "class Client:" in text and "Talks to the API." in text
    assert "def fetchRecord(self, record_id):" in text and "def close(self):" in text
    assert "return self.http.get(record_id)" not in text and "self.http.close()" not in text
    assert unit.metadata == {"virtualRecordId": "vr-1", "blockId": client_group.id, "orgId": "org-1",
                             "isBlock": False, "isBlockGroup": True, "blockGroupIndex": client_group.index,
                             "unitKind": "class"}


def test_one_header_unit_per_class_and_none_for_module_functions() -> None:
    container = _parse()
    docs = code_documents_for_record(_code_blocks(container), container.block_groups, "vr-1", "org-1")
    classes = [d for d in docs if d.metadata.get("unitKind") == "class"]
    assert len(classes) == len(_classes(container)) == 2
    assert not any("def helper" in d.page_content for d in classes)


def test_a_header_unit_stays_within_the_unit_budget_however_many_members() -> None:
    methods = "".join(f"    def method_{i}(self, first_argument, second_argument):\n        return {i}\n\n"
                      for i in range(400))
    container = _parse(f'class Huge:\n    """Many methods."""\n\n{methods}'.encode())
    docs = code_documents_for_record(_code_blocks(container), container.block_groups, "vr-1", "org-1")
    (unit,) = [d for d in docs if d.metadata.get("unitKind") == "class"]
    assert _token_len(unit.page_content) <= UNIT_TOKENS
    assert "class Huge:" in unit.page_content and "def method_0(" in unit.page_content
    assert "def method_399(" not in unit.page_content  # cut, not overflowing


def test_unchanged_code_keeps_its_block_hashes_and_only_classes_change() -> None:
    container = _parse()
    for block in _code_blocks(container):
        assert block.content_hash == hashlib.sha256(block.data["text"].encode()).hexdigest()
    for group in container.block_groups:
        assert group.content_hash != hashlib.sha256(group.data["text"].encode()).hexdigest()
