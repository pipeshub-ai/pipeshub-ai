#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Tableau (tableauserverclient) -- Code Generator

Emits a `TableauDataSource` with typed methods mapped to *real* TSC SDK APIs.
Follows the GitLab code-generator pattern: METHODS tuple, HEADER/FOOTER, _emit_method.

SDK Reference: https://tableau.github.io/server-client-python/docs/

Note: Most `.get()` calls return `(items_list, PaginationItem)` tuples.
Single-item fetches like `.get_by_id()` return the item directly.
"""

import argparse
import textwrap
from typing import List, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = (
    "from app.sources.client.tableau.tableau import TableauResponse"
)
DEFAULT_CLASS_NAME = "TableauDataSource"
DEFAULT_OUT = "app/sources/external/tableau/tableau.py"


HEADER = '''\
# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingImports=false, reportUnusedVariable=false
from __future__ import annotations

import tableauserverclient as TSC
from typing import Any, Dict, List, Union, cast

{response_import}


class {class_name}:
    """
    Typed wrapper over tableauserverclient for common Tableau business operations.

    Accepts either a TSC.Server instance or any client exposing `.get_sdk() -> TSC.Server`.

    SDK Reference: https://tableau.github.io/server-client-python/docs/
    """

    def __init__(self, client_or_sdk: Union[TSC.Server, object]) -> None:
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: TSC.Server = cast(TSC.Server, sdk_obj)
        else:
            self._sdk = cast(TSC.Server, client_or_sdk)

    @staticmethod
    def _to_dict(item: object) -> Dict[str, Any]:
        """Convert a TSC resource item to a dictionary representation."""
        if hasattr(item, "__dict__"):
            return {k: v for k, v in item.__dict__.items() if not k.startswith("_")}
        return {"value": str(item)}

    @staticmethod
    def _to_dict_list(items: object) -> List[Dict[str, Any]]:
        """Convert a list of TSC resource items to a list of dictionaries."""
        result: List[Dict[str, Any]] = []
        if hasattr(items, "__iter__"):
            for item in items:  # type: ignore[union-attr]
                if hasattr(item, "__dict__"):
                    result.append({k: v for k, v in item.__dict__.items() if not k.startswith("_")})
                else:
                    result.append({"value": str(item)})
        return result
'''

FOOTER = """
"""

# Each tuple: (signature, body, short_doc)
METHODS: List[Tuple[str, str, str]] = []

# ---------- Workbooks ----------
METHODS += [
    (
        "list_workbooks(self) -> TableauResponse",
        "            items, pagination = self._sdk.workbooks.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all workbooks on the site.  [workbooks]",
    ),
    (
        "get_workbook(self, workbook_id: str) -> TableauResponse",
        "            item = self._sdk.workbooks.get_by_id(workbook_id)\n"
        "            return TableauResponse(success=True, data=self._to_dict(item))",
        "Get a single workbook by ID.  [workbooks]",
    ),
    (
        "populate_workbook_views(self, workbook_id: str) -> TableauResponse",
        "            workbook = self._sdk.workbooks.get_by_id(workbook_id)\n"
        "            self._sdk.workbooks.populate_views(workbook)\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(workbook.views))",
        "Populate and return the views for a workbook.  [workbooks]",
    ),
    (
        "populate_workbook_connections(self, workbook_id: str) -> TableauResponse",
        "            workbook = self._sdk.workbooks.get_by_id(workbook_id)\n"
        "            self._sdk.workbooks.populate_connections(workbook)\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(workbook.connections))",
        "Populate and return the connections for a workbook.  [workbooks]",
    ),
    (
        "delete_workbook(self, workbook_id: str) -> TableauResponse",
        "            self._sdk.workbooks.delete(workbook_id)\n"
        "            return TableauResponse(success=True, data=True)",
        "Delete a workbook by ID.  [workbooks]",
    ),
]

# ---------- Views ----------
METHODS += [
    (
        "list_views(self) -> TableauResponse",
        "            items, pagination = self._sdk.views.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all views on the site.  [views]",
    ),
    (
        "get_view(self, view_id: str) -> TableauResponse",
        "            item = self._sdk.views.get_by_id(view_id)\n"
        "            return TableauResponse(success=True, data=self._to_dict(item))",
        "Get a single view by ID.  [views]",
    ),
]

# ---------- Data Sources ----------
METHODS += [
    (
        "list_datasources(self) -> TableauResponse",
        "            items, pagination = self._sdk.datasources.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all published data sources on the site.  [datasources]",
    ),
    (
        "get_datasource(self, datasource_id: str) -> TableauResponse",
        "            item = self._sdk.datasources.get_by_id(datasource_id)\n"
        "            return TableauResponse(success=True, data=self._to_dict(item))",
        "Get a single data source by ID.  [datasources]",
    ),
    (
        "delete_datasource(self, datasource_id: str) -> TableauResponse",
        "            self._sdk.datasources.delete(datasource_id)\n"
        "            return TableauResponse(success=True, data=True)",
        "Delete a data source by ID.  [datasources]",
    ),
]

# ---------- Projects ----------
METHODS += [
    (
        "list_projects(self) -> TableauResponse",
        "            items, pagination = self._sdk.projects.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all projects on the site.  [projects]",
    ),
]

# ---------- Users ----------
METHODS += [
    (
        "list_users(self) -> TableauResponse",
        "            items, pagination = self._sdk.users.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all users on the site.  [users]",
    ),
    (
        "get_user(self, user_id: str) -> TableauResponse",
        "            item = self._sdk.users.get_by_id(user_id)\n"
        "            return TableauResponse(success=True, data=self._to_dict(item))",
        "Get a single user by ID.  [users]",
    ),
]

# ---------- Groups ----------
METHODS += [
    (
        "list_groups(self) -> TableauResponse",
        "            items, pagination = self._sdk.groups.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all groups on the site.  [groups]",
    ),
    (
        "get_group(self, group_id: str) -> TableauResponse",
        "            item = self._sdk.groups.get_by_id(group_id)\n"
        "            return TableauResponse(success=True, data=self._to_dict(item))",
        "Get a single group by ID.  [groups]",
    ),
]

# ---------- Schedules ----------
METHODS += [
    (
        "list_schedules(self) -> TableauResponse",
        "            items, pagination = self._sdk.schedules.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all schedules on the server.  [schedules]",
    ),
]

# ---------- Jobs ----------
METHODS += [
    (
        "list_jobs(self) -> TableauResponse",
        "            items, pagination = self._sdk.jobs.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all jobs on the site.  [jobs]",
    ),
    (
        "get_job(self, job_id: str) -> TableauResponse",
        "            item = self._sdk.jobs.get_by_id(job_id)\n"
        "            return TableauResponse(success=True, data=self._to_dict(item))",
        "Get a single job by ID.  [jobs]",
    ),
]

# ---------- Flows ----------
METHODS += [
    (
        "list_flows(self) -> TableauResponse",
        "            items, pagination = self._sdk.flows.get()\n"
        "            return TableauResponse(success=True, data=self._to_dict_list(items))",
        "List all flows on the site.  [flows]",
    ),
    (
        "get_flow(self, flow_id: str) -> TableauResponse",
        "            item = self._sdk.flows.get_by_id(flow_id)\n"
        "            return TableauResponse(success=True, data=self._to_dict(item))",
        "Get a single flow by ID.  [flows]",
    ),
]

# ---------- Auth ----------
METHODS += [
    (
        "sign_out(self) -> TableauResponse",
        "            self._sdk.auth.sign_out()\n"
        "            return TableauResponse(success=True, data=True, message='Signed out successfully')",
        "Sign out and invalidate the current auth session.  [auth]",
    ),
]


# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "        ")
    return f'    def {sig}:\n        """{doc}"""\n{normalized_body}\n'


def build_class(
    response_import: str = DEFAULT_RESPONSE_IMPORT,
    class_name: str = DEFAULT_CLASS_NAME,
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
    from pathlib import Path

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate TableauDataSource (tableauserverclient)."
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output path for the generated data source.",
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in TableauResponse.",
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

    code = build_class(
        response_import=args.response_import, class_name=args.class_name
    )
    write_output(args.out, code)
    print(f"Generated {DEFAULT_CLASS_NAME} with {len(METHODS)} methods -> {args.out}")
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
