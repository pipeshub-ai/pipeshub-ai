"""Code blocks must reach Qdrant as whole symbols.

Sentence-splitting is meaningless for code, and the classifier in
index_documents silently drops any block type it does not recognise -- so a
missing branch here would leave code in the graph but absent from search.
"""
from app.modules.parsers.code_parser import CodeFileParser
from app.modules.transformers.vectorstore import _build_code_documents

SRC = b'''
class Client:
    """Talks to the API."""

    def fetchRecord(self, record_id):
        """Fetch one record."""
        return self.http.get(record_id)
'''


def _code_blocks(container):
    return [b for b in container.blocks if str(b.type.value) == "code"]


def _docs():
    container = CodeFileParser().parse_to_blocks(SRC, "client.py", "src/client.py", "python")
    return _build_code_documents(_code_blocks(container), "vrid1", "org1")


def _doc_for(kind_prefix: str):
    return next(d for d in _docs() if kind_prefix in d.page_content)


def test_one_document_per_block_not_per_sentence():
    docs = _docs()
    # A class yields a header block plus one block per member; each is embedded
    # whole rather than split into sentences.
    assert len(docs) == len(_code_blocks(
        CodeFileParser().parse_to_blocks(SRC, "client.py", "src/client.py", "python")
    ))
    assert all(d.metadata["isBlock"] is True for d in docs)


def test_document_carries_the_retrieval_metadata_contract():
    meta = _docs()[0].metadata
    assert meta["virtualRecordId"] == "vrid1"
    assert meta["orgId"] == "org1"
    assert meta["isBlockGroup"] is False
    assert meta["blockId"]


def test_docstring_and_qualified_name_lead_the_embedded_text():
    content = _doc_for("method:Client.fetchRecord").page_content
    assert "Fetch one record." in content
    assert "return self.http.get(record_id)" in content


def test_class_header_is_embedded_so_the_declaration_is_searchable():
    # The class signature and docstring live only in the group's text otherwise,
    # and code groups are never embedded.
    content = _doc_for("header").page_content
    assert "class Client:" in content
    assert "Talks to the API." in content


def test_subtokens_are_appended_for_bm25_recall():
    content = _doc_for("method:Client.fetchRecord").page_content
    # fetchRecord splits so a query for "fetch" or "record" can hit it.
    assert "fetch" in content
    assert "record" in content


def test_blocks_without_text_are_skipped():
    container = CodeFileParser().parse_to_blocks(SRC, "client.py", "src/client.py", "python")
    blocks = _code_blocks(container)
    for block in blocks:
        block.data = {}
        block.code_metadata = None
    assert _build_code_documents(blocks, "vrid1", "org1") == []
