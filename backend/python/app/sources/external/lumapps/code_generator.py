# ruff: noqa
"""
LumApps DataSource Code Generator

Defines LumApps SDK method specifications and generates the DataSource
wrapper class (lumapps.py) from them.

Methods wrap the official lumapps-sdk Python package.
The SDK uses a generic ``get_call`` / ``iter_call`` pattern.
"""

from __future__ import annotations

# Each spec:
#   name: method name
#   section: section heading
#   doc: docstring line
#   sdk_call: Python expression using `self._sdk` (the BaseClient instance)
#   params: list of (param_name, param_type, default_or_None, doc_line)
METHODS = [
    # ---- Users ----
    {
        "name": "list_users",
        "section": "Users",
        "doc": "List all users.",
        "sdk_call": 'self._sdk.get_call("user/list")',
        "params": [],
    },
    {
        "name": "get_user",
        "section": "Users",
        "doc": "Get a specific user by email.",
        "sdk_call": 'self._sdk.get_call("user/get", email=email)',
        "params": [("email", "str", None, "The user email address")],
    },
    {
        "name": "get_user_by_id",
        "section": "Users",
        "doc": "Get a specific user by ID.",
        "sdk_call": 'self._sdk.get_call("user/get", uid=user_id)',
        "params": [("user_id", "str", None, "The user ID")],
    },
    # ---- Communities ----
    {
        "name": "list_communities",
        "section": "Communities",
        "doc": "List all communities.",
        "sdk_call": 'self._sdk.get_call("community/list")',
        "params": [],
    },
    {
        "name": "get_community",
        "section": "Communities",
        "doc": "Get a specific community by ID.",
        "sdk_call": 'self._sdk.get_call("community/get", uid=community_id)',
        "params": [("community_id", "str", None, "The community ID")],
    },
    # ---- Content ----
    {
        "name": "list_content",
        "section": "Content",
        "doc": "List all content items.",
        "sdk_call": 'self._sdk.get_call("content/list")',
        "params": [],
    },
    {
        "name": "get_content",
        "section": "Content",
        "doc": "Get a specific content item by ID.",
        "sdk_call": 'self._sdk.get_call("content/get", uid=content_id)',
        "params": [("content_id", "str", None, "The content ID")],
    },
    # ---- Feeds ----
    {
        "name": "list_feeds",
        "section": "Feeds",
        "doc": "List all feeds.",
        "sdk_call": 'self._sdk.get_call("feed/list")',
        "params": [],
    },
    {
        "name": "get_feed",
        "section": "Feeds",
        "doc": "Get a specific feed by ID.",
        "sdk_call": 'self._sdk.get_call("feed/get", uid=feed_id)',
        "params": [("feed_id", "str", None, "The feed ID")],
    },
    # ---- Search ----
    {
        "name": "search",
        "section": "Search",
        "doc": "Search across LumApps content.",
        "sdk_call": 'self._sdk.get_call("search", body=body)',
        "params": [
            ("query", "str", None, "Search query string"),
            ("content_types", "list[str] | None", "None", "Content type filters"),
            ("limit", "int | None", "None", "Maximum number of results"),
        ],
        "build_body": True,
    },
    # ---- Directories ----
    {
        "name": "list_directories",
        "section": "Directories",
        "doc": "List all directories.",
        "sdk_call": 'self._sdk.get_call("directory/list")',
        "params": [],
    },
    {
        "name": "get_directory",
        "section": "Directories",
        "doc": "Get a specific directory by ID.",
        "sdk_call": 'self._sdk.get_call("directory/get", uid=directory_id)',
        "params": [("directory_id", "str", None, "The directory ID")],
    },
    # ---- Spaces ----
    {
        "name": "list_spaces",
        "section": "Spaces",
        "doc": "List all spaces.",
        "sdk_call": 'self._sdk.get_call("space/list")',
        "params": [],
    },
]


def _gen_method(spec: dict) -> str:
    """Generate a single method from a spec."""
    name = spec["name"]
    doc = spec["doc"]
    sdk_call = spec["sdk_call"]
    params = spec.get("params", [])
    build_body = spec.get("build_body", False)

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

    # Build body dict if needed
    body_block = ""
    if build_body:
        lines = ['            body: dict[str, object] = {"query": query}']
        for p_name, _, p_default, _ in params:
            if p_default is not None and p_name != "query":
                lines.append(f"            if {p_name} is not None:")
                # Map Python snake_case to camelCase API keys
                api_key = p_name
                if p_name == "content_types":
                    api_key = "contentTypes"
                lines.append(f'                body["{api_key}"] = {p_name}')
        body_block = "\n".join(lines) + "\n"

    return f'''
    def {name}(
        {sig},
    ) -> LumAppsResponse:
        """{doc}{doc_args}
        Returns:
            LumAppsResponse with operation result
        """
        try:
{body_block}            result = {sdk_call}
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute {name}"
            )
'''


def generate_datasource() -> str:
    """Generate the full LumApps DataSource module code."""
    header = '''# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""
LumApps SDK DataSource - Auto-generated SDK wrapper

Generated from LumApps SDK method specifications.
Wraps the official lumapps-sdk Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Union, cast

from lumapps.api import BaseClient

from app.sources.client.lumapps.lumapps import LumAppsClient, LumAppsResponse


class LumAppsDataSource:
    """LumApps SDK DataSource

    Provides typed wrapper methods for LumApps SDK operations:
    - Users management
    - Communities management
    - Content management
    - Feeds management
    - Search
    - Directories management
    - Spaces management

    All methods return LumAppsResponse objects.
    """

    def __init__(self, client_or_sdk: Union[LumAppsClient, BaseClient, object]) -> None:
        """Initialize with LumAppsClient, raw SDK, or any wrapper with ``get_sdk()``.

        Args:
            client_or_sdk: LumAppsClient, BaseClient instance, or wrapper
        """
        if isinstance(client_or_sdk, BaseClient):
            self._sdk: BaseClient = client_or_sdk
        elif hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk = cast(BaseClient, sdk_obj)
        else:
            self._sdk = cast(BaseClient, client_or_sdk)
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
