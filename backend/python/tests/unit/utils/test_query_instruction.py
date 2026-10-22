import pytest

from app.utils.query_instruction import (
    BGE_QUERY_INSTRUCTION,
    instruct_query,
    is_bge_query_model,
)


class _Emb:
    def __init__(self, model: str | None = None, model_name: str | None = None) -> None:
        if model is not None:
            self.model = model
        if model_name is not None:
            self.model_name = model_name


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBEDDING_QUERY_INSTRUCTION", "true")


@pytest.mark.parametrize("name", [
    "BAAI/bge-large-en-v1.5", "BAAI/bge-base-en-v1.5", "BAAI/bge-small-en", "bge-large-en-v1.5",
    "baai/bge-large-en-v1.5", "bge-large:latest", "org/BGE-Base-EN-v1.5",
])
def test_bge_models_get_instruction(name: str) -> None:
    assert is_bge_query_model(name)
    assert instruct_query(_Emb(model=name), "q") == BGE_QUERY_INSTRUCTION + "q"
    assert instruct_query(_Emb(model_name=name), "q") == BGE_QUERY_INSTRUCTION + "q"


@pytest.mark.parametrize("name", [
    None, "", "BAAI/bge-m3", "bge-m3", "BAAI/bge-large-zh-v1.5", "BAAI/bge-reranker-base",
    "BAAI/bge-multilingual-gemma2", "text-embedding-3-small", "sentence-transformers/all-MiniLM-L6-v2",
    "openai/not-a-bge-large-en", "models/gemini-embedding-001",
])
def test_other_models_unchanged(name: str | None) -> None:
    assert not is_bge_query_model(name)
    assert instruct_query(_Emb(model=name), "  q  ") == "  q  "


def test_embeddings_without_model_attr_unchanged() -> None:
    assert instruct_query(object(), "q") == "q"


def test_index_time_embedding_is_not_instructed() -> None:
    """Documents go through embed_documents untouched; no indexing module imports the helper."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3] / "app"
    users = {str(p.relative_to(root)) for p in root.rglob("*.py") if "query_instruction" in p.read_text()}
    assert users == {
        "modules/retrieval/retrieval_service.py",
        "modules/transformers/entity_vectorstore.py",
        "agents/agent_loop/skills/semantic_index.py",
    }
    src = (root / "modules/transformers/vectorstore.py").read_text()
    assert "instruct_query" not in src


@pytest.mark.parametrize("value", [None, "false", "0"])
def test_flag_off_leaves_bge_query_unchanged(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("EMBEDDING_QUERY_INSTRUCTION")
    else:
        monkeypatch.setenv("EMBEDDING_QUERY_INSTRUCTION", value)
    assert instruct_query(_Emb(model="BAAI/bge-large-en-v1.5"), "  q ") == "  q "
