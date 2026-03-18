# ruff: noqa
"""
Bynder DataSource Code Generator

Defines Bynder SDK method specifications and generates the DataSource
wrapper class (bynder.py) from them.

Methods wrap the official bynder-sdk Python package.
"""

from __future__ import annotations

# Each spec:
#   name: method name
#   section: section heading
#   doc: docstring line
#   sdk_call: Python expression using `self._sdk` (the BynderClient SDK instance)
#   params: list of (param_name, param_type, default_or_None, doc_line)
METHODS = [
    # ---- Media ----
    {
        "name": "get_media_list",
        "section": "Media",
        "doc": "List media assets.",
        "sdk_call": "self._asset_bank.media_list(query)",
        "params": [
            ("limit", "int | None", "None", "Maximum number of results"),
            ("page", "int | None", "None", "Page number for pagination"),
            ("keyword", "str | None", "None", "Filter by keyword"),
            ("type", "str | None", "None", "Filter by media type"),
        ],
        "build_query": True,
    },
    {
        "name": "get_media",
        "section": "Media",
        "doc": "Get a specific media asset by ID.",
        "sdk_call": "self._asset_bank.media_info(media_id)",
        "params": [("media_id", "str", None, "The media asset ID")],
    },
    {
        "name": "get_media_download_url",
        "section": "Media",
        "doc": "Get the download URL for a media asset.",
        "sdk_call": "self._asset_bank.media_download_url(media_id)",
        "params": [("media_id", "str", None, "The media asset ID")],
    },
    # ---- Collections ----
    {
        "name": "get_collections",
        "section": "Collections",
        "doc": "List all collections.",
        "sdk_call": "self._collection_client.collections(query)",
        "params": [
            ("limit", "int | None", "None", "Maximum number of results"),
            ("page", "int | None", "None", "Page number for pagination"),
        ],
        "build_query": True,
    },
    {
        "name": "get_collection",
        "section": "Collections",
        "doc": "Get a specific collection by ID.",
        "sdk_call": "self._collection_client.collection_info(collection_id)",
        "params": [("collection_id", "str", None, "The collection ID")],
    },
    # ---- Tags ----
    {
        "name": "get_tags",
        "section": "Tags",
        "doc": "List all tags.",
        "sdk_call": "self._asset_bank.tags()",
        "params": [],
    },
    # ---- Metaproperties ----
    {
        "name": "get_metaproperties",
        "section": "Metaproperties",
        "doc": "List all metaproperties.",
        "sdk_call": "self._asset_bank.meta_properties()",
        "params": [],
    },
    {
        "name": "get_metaproperty",
        "section": "Metaproperties",
        "doc": "Get a specific metaproperty by ID.",
        "sdk_call": "self._asset_bank.meta_property_info(metaproperty_id)",
        "params": [("metaproperty_id", "str", None, "The metaproperty ID")],
    },
    # ---- Brands ----
    {
        "name": "get_brands",
        "section": "Brands",
        "doc": "List all brands.",
        "sdk_call": "self._asset_bank.brands()",
        "params": [],
    },
    # ---- Account Users ----
    {
        "name": "get_account_users",
        "section": "Account Users",
        "doc": "List all account users.",
        "sdk_call": "self._asset_bank.users()",
        "params": [],
    },
    # ---- Smartfilters ----
    {
        "name": "get_smartfilters",
        "section": "Smartfilters",
        "doc": "List all smartfilters.",
        "sdk_call": "self._asset_bank.smartfilters()",
        "params": [],
    },
]


def _gen_method(spec: dict) -> str:
    """Generate a single method from a spec."""
    name = spec["name"]
    doc = spec["doc"]
    sdk_call = spec["sdk_call"]
    params = spec.get("params", [])
    build_query = spec.get("build_query", False)

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

    # Build query dict if needed
    query_block = ""
    if build_query:
        lines = ["            query: dict[str, object] = {}"]
        for p_name, _, p_default, _ in params:
            if p_default is not None:
                lines.append(f"            if {p_name} is not None:")
                lines.append(f"                query['{p_name}'] = {p_name}")
        query_block = "\n".join(lines) + "\n"

    return f'''
    def {name}(
        {sig},
    ) -> BynderResponse:
        """{doc}{doc_args}
        Returns:
            BynderResponse with operation result
        """
        try:
{query_block}            result = {sdk_call}
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute {name}"
            )
'''


def generate_datasource() -> str:
    """Generate the full Bynder DataSource module code."""
    header = '''# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""
Bynder SDK DataSource - Auto-generated SDK wrapper

Generated from Bynder SDK method specifications.
Wraps the official bynder-sdk Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Union, cast

from bynder_sdk import BynderClient as BynderSDKClient

from app.sources.client.bynder.bynder import BynderClient, BynderResponse


class BynderDataSource:
    """Bynder SDK DataSource

    Provides typed wrapper methods for Bynder SDK operations:
    - Media asset management
    - Collections management
    - Tags management
    - Metaproperties management
    - Brands management
    - Account users management
    - Smartfilters

    All methods return BynderResponse objects.
    """

    def __init__(self, client_or_sdk: Union[BynderClient, BynderSDKClient, object]) -> None:
        """Initialize with BynderClient, raw SDK, or any wrapper with ``get_sdk()``.

        Args:
            client_or_sdk: BynderClient, BynderSDKClient instance, or wrapper
        """
        if isinstance(client_or_sdk, BynderSDKClient):
            self._sdk: BynderSDKClient = client_or_sdk
        elif hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk = cast(BynderSDKClient, sdk_obj)
        else:
            self._sdk = cast(BynderSDKClient, client_or_sdk)

        self._asset_bank = self._sdk.asset_bank_client
        self._collection_client = self._sdk.collection_client
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
