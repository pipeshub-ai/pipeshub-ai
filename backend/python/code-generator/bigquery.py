#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
BigQuery (google-cloud-bigquery) -- Code Generator (strict, no `Any`, no `None` passthrough)

Emits a `BigQueryDataSource` with explicit, typed methods mapped to *real* google-cloud-bigquery APIs.
- No `Any` in signatures or implementation.
- Never forwards None to the SDK (filters optionals).
- Accepts either a raw `bigquery.Client` instance or any client exposing `.get_sdk() -> bigquery.Client`.

SDK references:
- Query: client.query(query_string).result()
- List datasets: list(client.list_datasets())
- Get dataset: client.get_dataset(dataset_id)
- List tables: list(client.list_tables(dataset_id))
- Get table: client.get_table(table_ref)
- List jobs: list(client.list_jobs())
- Get job: client.get_job(job_id)
- Create dataset: client.create_dataset(dataset)
- Delete dataset: client.delete_dataset(dataset_id, delete_contents=...)
- Create table: client.create_table(table)
- Delete table: client.delete_table(table_ref)
- Get table schema: client.get_table(table_ref).schema
"""

import argparse
import textwrap
from typing import Dict, List, Optional, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.bigquery.bigquery import BigQueryResponse"
DEFAULT_CLASS_NAME = "BigQueryDataSource"
DEFAULT_OUT = "bigquery_data_source.py"


HEADER = '''\
# ruff: noqa
from __future__ import annotations

from google.cloud import bigquery  # type: ignore[import-untyped]
from typing import Dict, List, Optional, Union, cast

{response_import}

class {class_name}:
    """
    Strict, typed wrapper over google-cloud-bigquery for common BigQuery operations.

    Accepts either a google-cloud-bigquery `Client` instance *or* any object with `.get_sdk() -> bigquery.Client`.
    """

    def __init__(self, client_or_sdk: Union[bigquery.Client, object]) -> None:
        super().__init__()
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: bigquery.Client = cast(bigquery.Client, sdk_obj)
        else:
            self._sdk = cast(bigquery.Client, client_or_sdk)

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

# ---------- Query ----------
METHODS += [
    (
        "query(self, query_string: str, project: Optional[str] = None, location: Optional[str] = None) -> BigQueryResponse",
        "            job_config_kwargs = self._params()\n"
        "            query_kwargs = self._params(project=project, location=location)\n"
        "            query_job = self._sdk.query(query_string, **query_kwargs)\n"
        "            results = query_job.result()\n"
        "            rows = [dict(row) for row in results]\n"
        "            return BigQueryResponse(success=True, data=rows)",
        "Execute a SQL query and return results as list of dicts.",
    ),
]

# ---------- Datasets ----------
METHODS += [
    (
        "list_datasets(self, project: Optional[str] = None, max_results: Optional[int] = None) -> BigQueryResponse",
        "            params = self._params(project=project, max_results=max_results)\n"
        "            datasets = list(self._sdk.list_datasets(**params))\n"
        "            return BigQueryResponse(success=True, data=datasets)",
        "List datasets in the project.",
    ),
    (
        "get_dataset(self, dataset_id: str) -> BigQueryResponse",
        "            dataset = self._sdk.get_dataset(dataset_id)\n"
        "            return BigQueryResponse(success=True, data=dataset)",
        "Get a dataset by ID.",
    ),
    (
        "create_dataset(self, dataset_id: str, location: Optional[str] = None, description: Optional[str] = None) -> BigQueryResponse",
        "            dataset_ref = bigquery.Dataset(self._sdk.dataset(dataset_id))\n"
        "            if location is not None:\n"
        "                dataset_ref.location = location\n"
        "            if description is not None:\n"
        "                dataset_ref.description = description\n"
        "            dataset = self._sdk.create_dataset(dataset_ref)\n"
        "            return BigQueryResponse(success=True, data=dataset)",
        "Create a new dataset.",
    ),
    (
        "delete_dataset(self, dataset_id: str, delete_contents: bool = False) -> BigQueryResponse",
        "            self._sdk.delete_dataset(dataset_id, delete_contents=delete_contents)\n"
        "            return BigQueryResponse(success=True, data=True)",
        "Delete a dataset.",
    ),
]

# ---------- Tables ----------
METHODS += [
    (
        "list_tables(self, dataset_id: str, max_results: Optional[int] = None) -> BigQueryResponse",
        "            params = self._params(max_results=max_results)\n"
        "            tables = list(self._sdk.list_tables(dataset_id, **params))\n"
        "            return BigQueryResponse(success=True, data=tables)",
        "List tables in a dataset.",
    ),
    (
        "get_table(self, table_ref: str) -> BigQueryResponse",
        "            table = self._sdk.get_table(table_ref)\n"
        "            return BigQueryResponse(success=True, data=table)",
        "Get a table by reference (dataset.table).",
    ),
    (
        "create_table(self, table_ref: str, schema: Optional[List[Dict[str, str]]] = None) -> BigQueryResponse",
        "            table = bigquery.Table(table_ref)\n"
        "            if schema is not None and len(schema) > 0:\n"
        "                fields = [bigquery.SchemaField(f['name'], f.get('type', 'STRING'), mode=f.get('mode', 'NULLABLE')) for f in schema]\n"
        "                table.schema = fields\n"
        "            result = self._sdk.create_table(table)\n"
        "            return BigQueryResponse(success=True, data=result)",
        "Create a table with optional schema.",
    ),
    (
        "delete_table(self, table_ref: str) -> BigQueryResponse",
        "            self._sdk.delete_table(table_ref)\n"
        "            return BigQueryResponse(success=True, data=True)",
        "Delete a table.",
    ),
    (
        "get_table_schema(self, table_ref: str) -> BigQueryResponse",
        "            table = self._sdk.get_table(table_ref)\n"
        "            schema = table.schema\n"
        "            return BigQueryResponse(success=True, data=schema)",
        "Get the schema of a table.",
    ),
]

# ---------- Jobs ----------
METHODS += [
    (
        "list_jobs(self, project: Optional[str] = None, max_results: Optional[int] = None, state_filter: Optional[str] = None) -> BigQueryResponse",
        "            params = self._params(project=project, max_results=max_results, state_filter=state_filter)\n"
        "            jobs = list(self._sdk.list_jobs(**params))\n"
        "            return BigQueryResponse(success=True, data=jobs)",
        "List jobs in the project.",
    ),
    (
        "get_job(self, job_id: str, project: Optional[str] = None, location: Optional[str] = None) -> BigQueryResponse",
        "            params = self._params(project=project, location=location)\n"
        "            job = self._sdk.get_job(job_id, **params)\n"
        "            return BigQueryResponse(success=True, data=job)",
        "Get a job by ID.",
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
        description="Generate BigQueryDataSource (google-cloud-bigquery)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in BigQueryResponse.",
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
