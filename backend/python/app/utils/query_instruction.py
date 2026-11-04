"""Query-side instruction for BGE English embedding models.

The BGE model card asks for ``Represent this sentence for searching relevant
passages: `` in front of *queries* of the v1/v1.5 English models; passages are
embedded as-is, so stored vectors never change. Experimental: off unless
``EMBEDDING_QUERY_INSTRUCTION`` is true. bge-m3 needs no instruction and
the zh models use a different one, so neither matches.
"""

import re

from langchain_core.embeddings import Embeddings

from app.utils.env_utils import env_bool

BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

# Last path segment of the model id: "BAAI/bge-large-en-v1.5", "bge-large:latest".
_BGE_EN = re.compile(r"bge-(?:small|base|large)(?:-en)?(?:-v1\.5)?(?:[:@].*)?")


def is_bge_query_model(model_name: str | None) -> bool:
    return bool(model_name) and _BGE_EN.fullmatch(model_name.lower().rsplit("/", 1)[-1]) is not None


def instruct_query(embeddings: Embeddings, query: str) -> str:
    """``query`` as the dense ``embeddings`` model should see it. Queries only."""
    if not env_bool("EMBEDDING_QUERY_INSTRUCTION", False):
        return query
    name = next(
        (v for a in ("model", "model_name") if isinstance(v := getattr(embeddings, a, None), str) and v),
        None,
    )
    return BGE_QUERY_INSTRUCTION + query if is_bge_query_model(name) else query
