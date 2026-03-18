#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
Splunk (splunk-sdk) -- Code Generator (strict, no `Any`, no `None` passthrough)

Emits a `SplunkDataSource` with explicit, typed methods mapped to *real* splunk-sdk APIs.
- No `Any` in signatures or implementation.
- Never forwards None to the SDK (filters optionals).
- Accepts either a raw `splunklib.client.Service` instance or any client exposing `.get_sdk() -> Service`.

SDK references:
- Search: service.jobs.create(query)
- Saved searches: service.saved_searches
- Indexes: service.indexes
- Apps: service.apps
- Users: service.users
- Jobs: service.jobs
- Inputs: service.inputs
- Server info: service.info
"""

import argparse
import textwrap
from typing import Dict, List, Optional, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = "from app.sources.client.splunk.splunk import SplunkResponse"
DEFAULT_CLASS_NAME = "SplunkDataSource"
DEFAULT_OUT = "splunk_data_source.py"


HEADER = '''\
# ruff: noqa
from __future__ import annotations

import splunklib.client as splunk_client  # type: ignore[import-untyped]
import splunklib.results as splunk_results  # type: ignore[import-untyped]
from typing import Dict, List, Optional, Union, cast

{response_import}

class {class_name}:
    """
    Strict, typed wrapper over splunk-sdk for common Splunk operations.

    Accepts either a splunklib `Service` instance *or* any object with `.get_sdk() -> Service`.
    """

    def __init__(self, client_or_sdk: Union[splunk_client.Service, object]) -> None:
        super().__init__()
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: splunk_client.Service = cast(splunk_client.Service, sdk_obj)
        else:
            self._sdk = cast(splunk_client.Service, client_or_sdk)

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

# ---------- Server Info ----------
METHODS += [
    (
        "get_server_info(self) -> SplunkResponse",
        "            info = self._sdk.info\n"
        "            return SplunkResponse(success=True, data=info)",
        "Get Splunk server information.",
    ),
]

# ---------- Search ----------
METHODS += [
    (
        "search(self, query: str, earliest_time: Optional[str] = None, latest_time: Optional[str] = None, max_count: Optional[int] = None, exec_mode: Optional[str] = None) -> SplunkResponse",
        "            params = self._params(earliest_time=earliest_time, latest_time=latest_time, max_count=max_count, exec_mode=exec_mode)\n"
        "            job = self._sdk.jobs.create(query, **params)\n"
        "            while not job.is_done():\n"
        "                import time\n"
        "                time.sleep(0.5)\n"
        "            rr = splunk_results.JSONResultsReader(job.results(output_mode='json'))\n"
        "            results = [result for result in rr if isinstance(result, dict)]\n"
        "            return SplunkResponse(success=True, data=results)",
        "Run a search query and return results.",
    ),
]

# ---------- Saved Searches ----------
METHODS += [
    (
        "list_saved_searches(self) -> SplunkResponse",
        "            items = list(self._sdk.saved_searches)\n"
        "            return SplunkResponse(success=True, data=items)",
        "List all saved searches.",
    ),
    (
        "get_saved_search(self, name: str) -> SplunkResponse",
        "            ss = self._sdk.saved_searches[name]\n"
        "            return SplunkResponse(success=True, data=ss)",
        "Get a saved search by name.",
    ),
]

# ---------- Indexes ----------
METHODS += [
    (
        "list_indexes(self) -> SplunkResponse",
        "            items = list(self._sdk.indexes)\n"
        "            return SplunkResponse(success=True, data=items)",
        "List all indexes.",
    ),
    (
        "get_index(self, name: str) -> SplunkResponse",
        "            idx = self._sdk.indexes[name]\n"
        "            return SplunkResponse(success=True, data=idx)",
        "Get an index by name.",
    ),
]

# ---------- Apps ----------
METHODS += [
    (
        "list_apps(self) -> SplunkResponse",
        "            items = list(self._sdk.apps)\n"
        "            return SplunkResponse(success=True, data=items)",
        "List all installed apps.",
    ),
    (
        "get_app(self, name: str) -> SplunkResponse",
        "            app = self._sdk.apps[name]\n"
        "            return SplunkResponse(success=True, data=app)",
        "Get an app by name.",
    ),
]

# ---------- Users ----------
METHODS += [
    (
        "list_users(self) -> SplunkResponse",
        "            items = list(self._sdk.users)\n"
        "            return SplunkResponse(success=True, data=items)",
        "List all users.",
    ),
]

# ---------- Jobs ----------
METHODS += [
    (
        "list_jobs(self) -> SplunkResponse",
        "            items = list(self._sdk.jobs)\n"
        "            return SplunkResponse(success=True, data=items)",
        "List all search jobs.",
    ),
    (
        "get_job(self, sid: str) -> SplunkResponse",
        "            job = self._sdk.jobs[sid]\n"
        "            return SplunkResponse(success=True, data=job)",
        "Get a search job by SID.",
    ),
]

# ---------- Inputs ----------
METHODS += [
    (
        "list_inputs(self) -> SplunkResponse",
        "            items = list(self._sdk.inputs)\n"
        "            return SplunkResponse(success=True, data=items)",
        "List all data inputs.",
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
        description="Generate SplunkDataSource (splunk-sdk)."
    )
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help="Output path for the generated data source."
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in SplunkResponse.",
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
