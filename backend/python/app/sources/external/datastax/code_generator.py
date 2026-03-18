# ruff: noqa
"""
DataStax DataSource Code Generator

Defines DataStax Astra DB SDK method specifications and generates the DataSource
wrapper class (datastax.py) from them.

Methods wrap the official astrapy Python package (Data API).
"""

from __future__ import annotations

# Each spec:
#   name: method name
#   section: section heading
#   doc: docstring line
#   body: list of Python lines forming the method body (using self._sdk, self._database)
#   params: list of (param_name, param_type, default_or_None, doc_line)
METHODS = [
    # ---- Databases ----
    {
        "name": "list_databases",
        "section": "Databases",
        "doc": "List all databases accessible via the admin API.",
        "body": [
            "admin = self._sdk.get_admin()",
            "dbs = list(admin.list_databases())",
            "result = [str(db) for db in dbs]",
        ],
        "params": [],
    },
    # ---- Collections ----
    {
        "name": "list_collections",
        "section": "Collections",
        "doc": "List all collection names in the database.",
        "body": [
            "result = self._database.list_collection_names()",
        ],
        "params": [],
    },
    {
        "name": "get_collection_info",
        "section": "Collections",
        "doc": "Get information about a collection by listing and filtering.",
        "body": [
            "names = self._database.list_collection_names()",
            "found = collection_name in names",
            'result = {"name": collection_name, "exists": found}',
        ],
        "params": [("collection_name", "str", None, "The collection name")],
    },
    # ---- Documents ----
    {
        "name": "find_documents",
        "section": "Documents",
        "doc": "Find documents in a collection with optional filter.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            "filter_dict = filter or {}",
            "cursor = collection.find(filter_dict, limit=limit or 20)",
            "result = list(cursor)",
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("filter", "dict[str, object] | None", "None", "Filter criteria"),
            ("limit", "int | None", "None", "Maximum documents to return"),
        ],
    },
    {
        "name": "find_one",
        "section": "Documents",
        "doc": "Find a single document by filter.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            "filter_dict = filter or {}",
            "result = collection.find_one(filter_dict)",
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("filter", "dict[str, object] | None", "None", "Filter criteria"),
        ],
    },
    {
        "name": "find_by_id",
        "section": "Documents",
        "doc": "Find a single document by its ``_id``.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            'result = collection.find_one({"_id": document_id})',
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("document_id", "str", None, "The document _id"),
        ],
    },
    {
        "name": "insert_one",
        "section": "Documents",
        "doc": "Insert a single document into a collection.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            "insert_result = collection.insert_one(document)",
            "result = insert_result",
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("document", "dict[str, object]", None, "The document to insert"),
        ],
    },
    {
        "name": "insert_many",
        "section": "Documents",
        "doc": "Insert multiple documents into a collection.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            "insert_result = collection.insert_many(documents)",
            "result = insert_result",
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("documents", "list[dict[str, object]]", None, "List of documents to insert"),
        ],
    },
    {
        "name": "update_one",
        "section": "Documents",
        "doc": "Update a single document matching the filter.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            "update_result = collection.update_one(filter, update)",
            "result = update_result",
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("filter", "dict[str, object]", None, "Filter criteria"),
            ("update", "dict[str, object]", None, "Update operations"),
        ],
    },
    {
        "name": "delete_one",
        "section": "Documents",
        "doc": "Delete a single document matching the filter.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            "delete_result = collection.delete_one(filter)",
            "result = delete_result",
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("filter", "dict[str, object]", None, "Filter criteria"),
        ],
    },
    {
        "name": "count_documents",
        "section": "Documents",
        "doc": "Count documents in a collection matching an optional filter.",
        "body": [
            "collection = self._database.get_collection(collection_name)",
            "filter_dict = filter or {}",
            "result = collection.count_documents(filter_dict, upper_bound=upper_bound or 1000)",
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
            ("filter", "dict[str, object] | None", "None", "Filter criteria"),
            ("upper_bound", "int | None", "None", "Upper bound for count estimation"),
        ],
    },
    # ---- Collection Management ----
    {
        "name": "create_collection",
        "section": "Collection Management",
        "doc": "Create a new collection in the database.",
        "body": [
            "collection = self._database.create_collection(collection_name)",
            'result = {"name": collection_name, "created": True}',
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
        ],
    },
    {
        "name": "drop_collection",
        "section": "Collection Management",
        "doc": "Drop (delete) a collection from the database.",
        "body": [
            "self._database.drop_collection(collection_name)",
            'result = {"name": collection_name, "dropped": True}',
        ],
        "params": [
            ("collection_name", "str", None, "The collection name"),
        ],
    },
]


def _gen_method(spec: dict) -> str:
    """Generate a single method from a spec."""
    name = spec["name"]
    doc = spec["doc"]
    body_lines = spec["body"]
    params = spec.get("params", [])

    # Build signature
    sig_parts = ["self"]
    has_kw_only = False
    for p_name, p_type, p_default, _ in params:
        if p_default is not None and not has_kw_only:
            sig_parts.append("*")
            has_kw_only = True
        if p_default is None:
            sig_parts.append(f"{p_name}: {p_type}")
        else:
            sig_parts.append(f"{p_name}: {p_type} = {p_default}")

    sig = ",\n        ".join(sig_parts)

    # Build docstring args section
    doc_args = ""
    if params:
        doc_args = "\n\n        Args:\n"
        for p_name, _, _, p_doc in params:
            doc_args += f"            {p_name}: {p_doc}\n"

    # Build body
    body = "\n".join(f"            {line}" for line in body_lines)

    return f'''
    def {name}(
        {sig},
    ) -> DataStaxResponse:
        """{doc}{doc_args}
        Returns:
            DataStaxResponse with operation result
        """
        try:
{body}
            return DataStaxResponse(success=True, data=result)
        except Exception as e:
            return DataStaxResponse(
                success=False, error=str(e), message="Failed to execute {name}"
            )
'''


def generate_datasource() -> str:
    """Generate the full DataStax DataSource module code."""
    header = '''# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""
DataStax Astra DB SDK DataSource - Auto-generated SDK wrapper

Generated from DataStax SDK method specifications.
Wraps the official astrapy Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Union, cast

from astrapy import DataAPIClient

from app.sources.client.datastax.datastax import DataStaxClient, DataStaxResponse


class DataStaxDataSource:
    """DataStax Astra DB SDK DataSource

    Provides typed wrapper methods for DataStax Astra DB operations:
    - Database listing
    - Collection management
    - Document CRUD operations

    All methods return DataStaxResponse objects.
    """

    def __init__(self, client_or_sdk: Union[DataStaxClient, DataAPIClient, object]) -> None:
        """Initialize with DataStaxClient, raw SDK, or any wrapper with ``get_sdk()``.

        The ``api_endpoint`` must be provided either via the DataStaxClient wrapper
        or passed when constructing with a raw SDK.

        Args:
            client_or_sdk: DataStaxClient, DataAPIClient instance, or wrapper
        """
        if isinstance(client_or_sdk, DataStaxClient):
            self._sdk: DataAPIClient = client_or_sdk.get_sdk()
            self._api_endpoint: str = client_or_sdk.get_api_endpoint()
        elif isinstance(client_or_sdk, DataAPIClient):
            self._sdk = client_or_sdk
            self._api_endpoint = ""
        elif hasattr(client_or_sdk, "get_sdk"):
            self._sdk = cast(DataAPIClient, getattr(client_or_sdk, "get_sdk")())
            if hasattr(client_or_sdk, "get_api_endpoint"):
                self._api_endpoint = str(getattr(client_or_sdk, "get_api_endpoint")())
            else:
                self._api_endpoint = ""
        else:
            self._sdk = cast(DataAPIClient, client_or_sdk)
            self._api_endpoint = ""

        # Lazily connect to the database
        self._database = self._sdk.get_database(api_endpoint=self._api_endpoint) if self._api_endpoint else None

    def set_api_endpoint(self, api_endpoint: str) -> None:
        """Set the API endpoint and connect to the database.

        Args:
            api_endpoint: The database API endpoint URL
        """
        self._api_endpoint = api_endpoint
        self._database = self._sdk.get_database(api_endpoint=api_endpoint)

    def _ensure_database(self) -> None:
        """Ensure a database connection is established."""
        if self._database is None:
            raise ValueError(
                "No database connection. Provide api_endpoint via DataStaxClient "
                "or call set_api_endpoint()."
            )
'''

    methods = []
    current_section = None
    for spec in METHODS:
        section = spec.get("section", "")
        if section and section != current_section:
            current_section = section
            methods.append(
                f"\n    # {'-' * 71}\n    # {section}\n    # {'-' * 71}"
            )
        methods.append(_gen_method(spec))

    return header + "\n".join(methods) + "\n"


if __name__ == "__main__":
    print(generate_datasource())
