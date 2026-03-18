# ruff: noqa
"""
Benchling DataSource Code Generator

Defines Benchling SDK method specifications and generates the DataSource
wrapper class (benchling.py) from them.

Methods wrap the official benchling-sdk Python package.
"""

from __future__ import annotations

# Each spec:
#   name: method name
#   section: section heading
#   doc: docstring line
#   sdk_call: Python expression using `self._sdk` (the Benchling SDK instance)
#   params: list of (param_name, param_type, default_or_None, doc_line)
#     - if default_or_None is None  -> positional required
#     - if default_or_None is a string -> keyword with that default
METHODS = [
    # ---- Entries ----
    {
        "name": "list_entries",
        "section": "Entries",
        "doc": "List notebook entries.",
        "sdk_call": "list(self._sdk.entries.list())",
        "params": [],
    },
    {
        "name": "get_entry",
        "section": "Entries",
        "doc": "Get a single notebook entry by ID.",
        "sdk_call": "self._sdk.entries.get_by_id(entry_id)",
        "params": [("entry_id", "str", None, "The entry ID (e.g. ``etr_xxx``)")],
    },
    # ---- Folders ----
    {
        "name": "list_folders",
        "section": "Folders",
        "doc": "List folders.",
        "sdk_call": "list(self._sdk.folders.list())",
        "params": [],
    },
    {
        "name": "get_folder",
        "section": "Folders",
        "doc": "Get a single folder by ID.",
        "sdk_call": "self._sdk.folders.get_by_id(folder_id)",
        "params": [("folder_id", "str", None, "The folder ID")],
    },
    # ---- Entity Schemas ----
    {
        "name": "list_entity_schemas",
        "section": "Schemas",
        "doc": "List entity schemas.",
        "sdk_call": "list(self._sdk.schemas.list_entity_schemas())",
        "params": [],
    },
    {
        "name": "get_entity_schema",
        "section": "Schemas",
        "doc": "Get a single entity schema by ID.",
        "sdk_call": "self._sdk.schemas.get_entity_schema_by_id(schema_id)",
        "params": [("schema_id", "str", None, "The schema ID")],
    },
    # ---- Custom Entities ----
    {
        "name": "list_custom_entities",
        "section": "Custom Entities",
        "doc": "List custom entities.",
        "sdk_call": "list(self._sdk.custom_entities.list())",
        "params": [],
    },
    {
        "name": "get_custom_entity",
        "section": "Custom Entities",
        "doc": "Get a single custom entity by ID.",
        "sdk_call": "self._sdk.custom_entities.get_by_id(entity_id)",
        "params": [("entity_id", "str", None, "The custom entity ID")],
    },
    # ---- DNA Sequences ----
    {
        "name": "list_dna_sequences",
        "section": "DNA Sequences",
        "doc": "List DNA sequences.",
        "sdk_call": "list(self._sdk.dna_sequences.list())",
        "params": [],
    },
    {
        "name": "get_dna_sequence",
        "section": "DNA Sequences",
        "doc": "Get a single DNA sequence by ID.",
        "sdk_call": "self._sdk.dna_sequences.get_by_id(sequence_id)",
        "params": [("sequence_id", "str", None, "The DNA sequence ID")],
    },
    # ---- AA Sequences ----
    {
        "name": "list_aa_sequences",
        "section": "AA Sequences",
        "doc": "List AA (amino acid) sequences.",
        "sdk_call": "list(self._sdk.aa_sequences.list())",
        "params": [],
    },
    {
        "name": "get_aa_sequence",
        "section": "AA Sequences",
        "doc": "Get a single AA sequence by ID.",
        "sdk_call": "self._sdk.aa_sequences.get_by_id(sequence_id)",
        "params": [("sequence_id", "str", None, "The AA sequence ID")],
    },
    # ---- Oligos ----
    {
        "name": "list_oligos",
        "section": "Oligos",
        "doc": "List oligos.",
        "sdk_call": "list(self._sdk.oligos.list())",
        "params": [],
    },
    {
        "name": "get_oligo",
        "section": "Oligos",
        "doc": "Get a single oligo by ID.",
        "sdk_call": "self._sdk.oligos.get_by_id(oligo_id)",
        "params": [("oligo_id", "str", None, "The oligo ID")],
    },
    # ---- Users ----
    {
        "name": "list_users",
        "section": "Users",
        "doc": "List users.",
        "sdk_call": "list(self._sdk.users.list())",
        "params": [],
    },
    {
        "name": "get_user",
        "section": "Users",
        "doc": "Get a single user by ID.",
        "sdk_call": "self._sdk.users.get_by_id(user_id)",
        "params": [("user_id", "str", None, "The user ID")],
    },
    # ---- Projects ----
    {
        "name": "list_projects",
        "section": "Projects",
        "doc": "List projects.",
        "sdk_call": "list(self._sdk.projects.list())",
        "params": [],
    },
    {
        "name": "get_project",
        "section": "Projects",
        "doc": "Get a single project by ID.",
        "sdk_call": "self._sdk.projects.get_by_id(project_id)",
        "params": [("project_id", "str", None, "The project ID")],
    },
]


def _gen_method(spec: dict) -> str:
    """Generate a single method from a spec."""
    name = spec["name"]
    doc = spec["doc"]
    sdk_call = spec["sdk_call"]
    params = spec.get("params", [])

    # Build signature
    sig_parts = ["self"]
    for p_name, p_type, p_default, _ in params:
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

    return f'''
    def {name}(
        {sig},
    ) -> BenchlingResponse:
        """{doc}{doc_args}
        Returns:
            BenchlingResponse with operation result
        """
        try:
            result = {sdk_call}
            return BenchlingResponse(success=True, data=result)
        except Exception as e:
            return BenchlingResponse(
                success=False, error=str(e), message="Failed to execute {name}"
            )
'''


def generate_datasource() -> str:
    """Generate the full Benchling DataSource module code."""
    header = '''# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""
Benchling SDK DataSource - Auto-generated SDK wrapper

Generated from Benchling SDK method specifications.
Wraps the official benchling-sdk Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Union, cast

from benchling_sdk.benchling import Benchling

from app.sources.client.benchling.benchling import BenchlingClient, BenchlingResponse


class BenchlingDataSource:
    """Benchling SDK DataSource

    Provides typed wrapper methods for Benchling SDK operations:
    - Notebook entries
    - Folders
    - Entity schemas
    - Custom entities
    - DNA sequences
    - AA sequences
    - Oligos
    - Users
    - Projects

    All methods return BenchlingResponse objects.
    """

    def __init__(self, client_or_sdk: Union[BenchlingClient, Benchling, object]) -> None:
        """Initialize with BenchlingClient, raw SDK, or any wrapper with ``get_sdk()``.

        Args:
            client_or_sdk: BenchlingClient, Benchling SDK instance, or wrapper
        """
        if isinstance(client_or_sdk, Benchling):
            self._sdk: Benchling = client_or_sdk
        elif hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk = cast(Benchling, sdk_obj)
        else:
            self._sdk = cast(Benchling, client_or_sdk)
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
