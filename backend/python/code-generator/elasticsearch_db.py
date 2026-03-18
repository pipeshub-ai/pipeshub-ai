#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Elasticsearch (elasticsearch-py) -- Code Generator (strict, no `Any`, no `None` passthrough)

Emits an `ElasticsearchDataSource` with explicit, typed methods mapped to *real* elasticsearch-py APIs.
- No `Any` in signatures or implementation.
- Never forwards None to the SDK (filters optionals).
- Accepts either a raw `Elasticsearch` instance or any client exposing `.get_sdk() -> Elasticsearch`.

SDK references:
- Info: client.info()
- Search: client.search(index=..., body=...)
- Index: client.index(index=..., id=..., body=...)
- Get: client.get(index=..., id=...)
- Delete: client.delete(index=..., id=...)
- Bulk: client.bulk(body=..., index=...)
- Count: client.count(index=..., body=...)
- Mapping: client.indices.get_mapping(index=...)
- Indices: client.indices.get_alias(index="*")
- Create index: client.indices.create(index=..., body=...)
- Delete index: client.indices.delete(index=...)
- Cluster health: client.cluster.health()
- Cluster stats: client.cluster.stats()
- Scroll: client.scroll(scroll_id=..., scroll=...)
- Clear scroll: client.clear_scroll(scroll_id=...)
"""

import argparse
import textwrap
from typing import Dict, List, Optional, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.elasticsearch_db.elasticsearch_db import ElasticsearchResponse"
DEFAULT_CLASS_NAME = "ElasticsearchDataSource"
DEFAULT_OUT = "elasticsearch_data_source.py"


HEADER = '''\
# ruff: noqa
from __future__ import annotations

from elasticsearch import Elasticsearch  # type: ignore[import-untyped]
from typing import Dict, List, Optional, Union, cast

{response_import}

class {class_name}:
    """
    Strict, typed wrapper over elasticsearch-py for common Elasticsearch operations.

    Accepts either an elasticsearch-py `Elasticsearch` instance *or* any object with `.get_sdk() -> Elasticsearch`.
    """

    def __init__(self, client_or_sdk: Union[Elasticsearch, object]) -> None:
        super().__init__()
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: Elasticsearch = cast(Elasticsearch, sdk_obj)
        else:
            self._sdk = cast(Elasticsearch, client_or_sdk)

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        # Filter out Nones to avoid overriding SDK defaults
        out: Dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Cluster / Info ----------
METHODS += [
    (
        "info(self) -> ElasticsearchResponse",
        "            result = self._sdk.info()\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Get cluster info.",
    ),
    (
        "get_cluster_health(self) -> ElasticsearchResponse",
        "            result = self._sdk.cluster.health()\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Get cluster health status.",
    ),
    (
        "get_cluster_stats(self) -> ElasticsearchResponse",
        "            result = self._sdk.cluster.stats()\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Get cluster statistics.",
    ),
]

# ---------- Index management ----------
METHODS += [
    (
        "list_indices(self) -> ElasticsearchResponse",
        "            result = self._sdk.indices.get_alias(index='*')\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "List all indices and their aliases.",
    ),
    (
        "create_index(self, index: str, body: Optional[Dict[str, object]] = None) -> ElasticsearchResponse",
        "            params = self._params(index=index, body=body)\n"
        "            result = self._sdk.indices.create(**params)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Create an index with optional settings/mappings.",
    ),
    (
        "delete_index(self, index: str) -> ElasticsearchResponse",
        "            result = self._sdk.indices.delete(index=index)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Delete an index.",
    ),
    (
        "get_mapping(self, index: str) -> ElasticsearchResponse",
        "            result = self._sdk.indices.get_mapping(index=index)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Get mapping for an index.",
    ),
]

# ---------- Document operations ----------
METHODS += [
    (
        "search(self, index: str, body: Optional[Dict[str, object]] = None, size: Optional[int] = None, from_: Optional[int] = None, sort: Optional[str] = None) -> ElasticsearchResponse",
        "            params = self._params(index=index, body=body, size=size, sort=sort)\n"
        "            if from_ is not None:\n"
        "                params['from_'] = from_\n"
        "            result = self._sdk.search(**params)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Search documents in an index.",
    ),
    (
        "index_document(self, index: str, body: Dict[str, object], doc_id: Optional[str] = None) -> ElasticsearchResponse",
        "            params = self._params(index=index, body=body, id=doc_id)\n"
        "            result = self._sdk.index(**params)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Index (create/update) a document.",
    ),
    (
        "get_document(self, index: str, doc_id: str) -> ElasticsearchResponse",
        "            result = self._sdk.get(index=index, id=doc_id)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Get a document by ID.",
    ),
    (
        "delete_document(self, index: str, doc_id: str) -> ElasticsearchResponse",
        "            result = self._sdk.delete(index=index, id=doc_id)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Delete a document by ID.",
    ),
    (
        "count(self, index: str, body: Optional[Dict[str, object]] = None) -> ElasticsearchResponse",
        "            params = self._params(index=index, body=body)\n"
        "            result = self._sdk.count(**params)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Count documents in an index.",
    ),
    (
        "bulk(self, body: List[Dict[str, object]], index: Optional[str] = None) -> ElasticsearchResponse",
        "            params = self._params(body=body, index=index)\n"
        "            result = self._sdk.bulk(**params)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Perform bulk operations.",
    ),
]

# ---------- Scroll ----------
METHODS += [
    (
        "scroll(self, scroll_id: str, scroll: str = '5m') -> ElasticsearchResponse",
        "            result = self._sdk.scroll(scroll_id=scroll_id, scroll=scroll)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Continue a scroll search.",
    ),
    (
        "clear_scroll(self, scroll_id: str) -> ElasticsearchResponse",
        "            result = self._sdk.clear_scroll(scroll_id=scroll_id)\n"
        "            return ElasticsearchResponse(success=True, data=result)",
        "Clear a scroll context.",
    ),
]

# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "        ")
    return f'    def {sig}:\n        """{doc}"""\n{normalized_body}\n'


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT, class_name: str = DEFAULT_CLASS_NAME
) -> str:
    parts: List[str] = []
    header = HEADER.replace("{response_import}", response_import).replace(
        "{class_name}", class_name
    )
    parts.append(header)
    for sig, body, doc in METHODS:
        parts.append(_emit_method(sig, body, doc))
    parts.append(FOOTER)
    return "".join(parts)


def write_output(path: str, code: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ElasticsearchDataSource (elasticsearch-py)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in ElasticsearchResponse.",
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS_NAME,
        help="Name of the generated datasource class.",
    )
    parser.add_argument(
        "--print",
        dest="do_print",
        action="store_true",
        help="Also print generated code to stdout.",
    )
    args = parser.parse_args()

    code = build_class(response_import=args.response_import, class_name=args.class_name)
    write_output(args.out, code)
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
