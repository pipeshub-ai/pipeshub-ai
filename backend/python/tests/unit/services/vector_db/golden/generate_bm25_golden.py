"""Regenerate bm25_golden.json from the installed fastembed.

Run only when a re-index of stored sparse vectors is acceptable:
    python tests/unit/services/vector_db/golden/generate_bm25_golden.py
The corpus is hand-written below; the expected vectors come from the app's own
SparseEmbedder (embed_documents -> fastembed embed, embed_query -> query_embed).
"""
import asyncio
import json
import pathlib

from app.services.vector_db.sparse_embeddings import SparseEmbedder

DOCS = [
    "PipesHub indexes documents from Google Drive, Confluence and Jira into a searchable knowledge base.",
    "Reset your password from the account settings page, then sign in again with the new password.",
    "The connector sync failed with HTTP 429: rate limit exceeded; retry after 30 seconds.",
    "Running runners ran quickly; connections connected while connecting to the connection pool.",
    "def fetch(url: str, retries: int = 3) -> bytes:\n    return requests.get(url, timeout=10).content",
    "SELECT id, name FROM users WHERE email LIKE '%@example.com' AND active = 1 ORDER BY created_at DESC;",
    "Order #12345 totalled $1,299.99 on 2026-10-24 at 13:45:07 (SKU AB-1234/XY_9, v1.2.3-rc1).",
    "Café naïve résumé: Zürich, São Paulo and Málaga all appear in the 2025 travel report.",
    "東京は日本の首都です。北京は中国の首都です。서울은 한국의 수도입니다.",
    "Привет, мир! Это тестовый документ для проверки токенизации и стемминга.",
    "مرحبا بالعالم، هذا مستند اختبار. שלום עולם, זהו מסמך בדיקה.",
    "Emoji 😀🚀 and symbols © ® ™ € £ ¥ should not break tokenisation.",
    "https://docs.example.com/guide/getting-started?lang=en#install user@example.com 192.168.0.1",
    "The the THE The and of to in is it a an I me my we our you your",
    "Kubernetes deployment: helm upgrade --install pipeshub ./chart --set replicas=3 --namespace prod",
    "Quarterly revenue grew 12.5% year over year, reaching $4.2M in Q3 2026 versus $3.7M in Q3 2025.",
    "{\"name\": \"widget\", \"tags\": [\"a\", \"b\"], \"price\": 9.99, \"in_stock\": true}",
    "Meeting notes: Alice will draft the proposal; Bob reviews; Carol approves before Friday.",
    "The quick brown fox jumps over the lazy dog. Pack my box with five dozen liquor jugs.",
    "Authentication uses OAuth 2.0 with PKCE; tokens expire after 3600 seconds and are refreshed automatically.",
    "x",
    "!!! ??? ... --- ***",
    "",
    "Hyphenated-words, snake_case_identifiers and camelCaseIdentifiers are common in source code.",
    "Les élèves étudient la géométrie, l'algèbre et l'histoire. Die Straße ist gesperrt. Ñandú.",
    "Unicode normalisation: é (combining) versus é (precomposed); full-width ＡＢＣ１２３.",
    " ".join(["repeat"] * 40) + " tail",
    "Error: ECONNREFUSED 127.0.0.1:6333 - Qdrant is not reachable; check the container and port mapping.",
    "Vector search combines dense embeddings with BM25 sparse vectors for hybrid retrieval.",
    "Tabs\tand\nnewlines\r\nand   multiple    spaces   are   whitespace.",
]
QUERIES = [
    "how do I reset my password",
    "connector sync failed 429",
    "running connections",
    "Zürich São Paulo",
    "東京 首都",
    "SELECT users email",
    "v1.2.3-rc1",
    "the and of",
    "kubernetes helm replicas",
    "😀",
]


async def main() -> None:
    e = SparseEmbedder()
    docs = await e.embed_documents(DOCS)
    queries = [await e.embed_query(q) for q in QUERIES]
    out = {
        "docs": DOCS,
        "queries": QUERIES,
        "doc_vectors": [{"indices": [int(i) for i in v.indices], "values": [float(x) for x in v.values]} for v in docs],
        "query_vectors": [{"indices": [int(i) for i in v.indices], "values": [float(x) for x in v.values]} for v in queries],
    }
    path = pathlib.Path(__file__).with_name("bm25_golden.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    asyncio.run(main())
