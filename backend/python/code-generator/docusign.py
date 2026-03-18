#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

"""
DocuSign (docusign-esign SDK) -- Code Generator (strict, no `Any`, no `None` passthrough)

Emits a `DocuSignDataSource` with explicit, typed methods mapped to the real
docusign_esign SDK APIs.

- Never forwards None to the SDK (filters optionals via `_params` helper).
- Accepts either a `docusign_esign.ApiClient` instance or any client exposing
  `.get_sdk() -> docusign_esign.ApiClient` and `.get_account_id() -> str`.
- Lazily creates per-resource API instances (EnvelopesApi, TemplatesApi, etc.).

SDK mapping (for maintainers):
- EnvelopesApi:  list_status_changes, get_envelope, create_envelope, update,
                 list_documents, get_document, list_recipients, list_audit_events
- TemplatesApi:  list_templates, get
- UsersApi:      list, get_information
- FoldersApi:    list, list_items
- AccountsApi:   list_brands, list_custom_fields
"""

import argparse
import textwrap
from typing import List, Tuple

# -----------------------------
# Configuration knobs (CLI-set)
# -----------------------------

DEFAULT_RESPONSE_IMPORT = (
    "from app.sources.client.docusign.docusign import DocuSignResponse"
)
DEFAULT_CLASS_NAME = "DocuSignDataSource"
DEFAULT_OUT = "app/sources/external/docusign/docusign.py"

HEADER = '''\
# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
from __future__ import annotations

import docusign_esign
from docusign_esign import ApiClient
from typing import Dict, Optional, Union, cast

{response_import}


class {class_name}:
    """
    Strict, typed wrapper over the docusign-esign SDK for common DocuSign
    eSignature business operations.

    Accepts either a ``docusign_esign.ApiClient`` instance *or* any object with
    ``.get_sdk() -> ApiClient`` and ``.get_account_id() -> str``.

    Lazily creates per-resource API objects (EnvelopesApi, TemplatesApi, etc.)
    so that only the resources you actually call are instantiated.
    """

    def __init__(self, client_or_sdk: Union[ApiClient, object]) -> None:
        # Support a raw SDK or a wrapper that exposes `.get_sdk()`
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk: ApiClient = cast(ApiClient, sdk_obj)
        else:
            self._sdk = cast(ApiClient, client_or_sdk)

        # Resolve account_id
        if hasattr(client_or_sdk, "get_account_id"):
            self._account_id: str = str(getattr(client_or_sdk, "get_account_id")())
        else:
            self._account_id = ""

        # Lazy API instances
        self._envelopes_api: Optional[docusign_esign.EnvelopesApi] = None
        self._templates_api: Optional[docusign_esign.TemplatesApi] = None
        self._users_api: Optional[docusign_esign.UsersApi] = None
        self._folders_api: Optional[docusign_esign.FoldersApi] = None
        self._accounts_api: Optional[docusign_esign.AccountsApi] = None

    # ---- lazy accessors ----

    @property
    def envelopes_api(self) -> docusign_esign.EnvelopesApi:
        if self._envelopes_api is None:
            self._envelopes_api = docusign_esign.EnvelopesApi(self._sdk)
        return self._envelopes_api

    @property
    def templates_api(self) -> docusign_esign.TemplatesApi:
        if self._templates_api is None:
            self._templates_api = docusign_esign.TemplatesApi(self._sdk)
        return self._templates_api

    @property
    def users_api(self) -> docusign_esign.UsersApi:
        if self._users_api is None:
            self._users_api = docusign_esign.UsersApi(self._sdk)
        return self._users_api

    @property
    def folders_api(self) -> docusign_esign.FoldersApi:
        if self._folders_api is None:
            self._folders_api = docusign_esign.FoldersApi(self._sdk)
        return self._folders_api

    @property
    def accounts_api(self) -> docusign_esign.AccountsApi:
        if self._accounts_api is None:
            self._accounts_api = docusign_esign.AccountsApi(self._sdk)
        return self._accounts_api

    # ---- helpers ----
    @staticmethod
    def _params(**kwargs: object) -> Dict[str, object]:
        """Filter out Nones to avoid overriding SDK defaults."""
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

# ---------- Envelopes ----------
METHODS += [
    (
        # EnvelopesApi.list_status_changes(account_id, from_date=..., ...)
        "list_envelopes(self, from_date: str, to_date: Optional[str] = None, status: Optional[str] = None, search_text: Optional[str] = None, count: Optional[str] = None, start_position: Optional[str] = None, order: Optional[str] = None, order_by: Optional[str] = None, folder_ids: Optional[str] = None) -> DocuSignResponse",
        "            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position, order=order, order_by=order_by, folder_ids=folder_ids)\n"
        "            result = self.envelopes_api.list_status_changes(account_id=self._account_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List envelopes for the account. from_date is required by the API.  [envelopes]",
    ),
    (
        # EnvelopesApi.get_envelope(account_id, envelope_id)
        "get_envelope(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.get_envelope(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get details for a specific envelope.  [envelopes]",
    ),
    (
        # EnvelopesApi.create_envelope(account_id, envelope_definition=body)
        "create_envelope(self, envelope_definition: Dict[str, object]) -> DocuSignResponse",
        "            body = docusign_esign.EnvelopeDefinition(**envelope_definition)\n"
        "            result = self.envelopes_api.create_envelope(account_id=self._account_id, envelope_definition=body)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Create and optionally send a new envelope from an envelope definition dict.  [envelopes]",
    ),
    (
        # EnvelopesApi.update(account_id, envelope_id, envelope=body)
        "update_envelope(self, envelope_id: str, envelope: Dict[str, object]) -> DocuSignResponse",
        "            body = docusign_esign.Envelope(**envelope)\n"
        "            result = self.envelopes_api.update(account_id=self._account_id, envelope_id=envelope_id, envelope=body)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Update an existing envelope (e.g. change status to sent or voided).  [envelopes]",
    ),
    (
        # EnvelopesApi.list_documents(account_id, envelope_id)
        "list_envelope_documents(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.list_documents(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List documents in an envelope.  [envelopes]",
    ),
    (
        # EnvelopesApi.get_document(account_id, envelope_id, document_id)
        "get_envelope_document(self, envelope_id: str, document_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.get_document(account_id=self._account_id, envelope_id=envelope_id, document_id=document_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Download a specific document from an envelope.  [envelopes]",
    ),
    (
        # EnvelopesApi.list_recipients(account_id, envelope_id)
        "list_envelope_recipients(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.list_recipients(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List recipients for an envelope.  [envelopes]",
    ),
    (
        # EnvelopesApi.list_audit_events(account_id, envelope_id)
        "get_envelope_audit_events(self, envelope_id: str) -> DocuSignResponse",
        "            result = self.envelopes_api.list_audit_events(account_id=self._account_id, envelope_id=envelope_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get audit trail events for an envelope.  [envelopes]",
    ),
]

# ---------- Templates ----------
METHODS += [
    (
        # TemplatesApi.list_templates(account_id, ...)
        "list_templates(self, count: Optional[str] = None, start_position: Optional[str] = None, search_text: Optional[str] = None, folder: Optional[str] = None, order: Optional[str] = None, order_by: Optional[str] = None) -> DocuSignResponse",
        "            params = self._params(count=count, start_position=start_position, search_text=search_text, folder=folder, order=order, order_by=order_by)\n"
        "            result = self.templates_api.list_templates(account_id=self._account_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List templates for the account.  [templates]",
    ),
    (
        # TemplatesApi.get(account_id, template_id)
        "get_template(self, template_id: str) -> DocuSignResponse",
        "            result = self.templates_api.get(account_id=self._account_id, template_id=template_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get details for a specific template.  [templates]",
    ),
]

# ---------- Users ----------
METHODS += [
    (
        # UsersApi.list(account_id, ...)
        "list_users(self, count: Optional[str] = None, start_position: Optional[str] = None, status: Optional[str] = None, email: Optional[str] = None) -> DocuSignResponse",
        "            params = self._params(count=count, start_position=start_position, status=status, email=email)\n"
        "            result = self.users_api.list(account_id=self._account_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List users in the account.  [users]",
    ),
    (
        # UsersApi.get_information(account_id, user_id)
        "get_user(self, user_id: str) -> DocuSignResponse",
        "            result = self.users_api.get_information(account_id=self._account_id, user_id=user_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "Get details for a specific user.  [users]",
    ),
]

# ---------- Folders ----------
METHODS += [
    (
        # FoldersApi.list(account_id)
        "list_folders(self) -> DocuSignResponse",
        "            result = self.folders_api.list(account_id=self._account_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List folders in the account.  [folders]",
    ),
    (
        # FoldersApi.list_items(account_id, folder_id, ...)
        "list_folder_items(self, folder_id: str, from_date: Optional[str] = None, to_date: Optional[str] = None, status: Optional[str] = None, search_text: Optional[str] = None, count: Optional[str] = None, start_position: Optional[str] = None) -> DocuSignResponse",
        "            params = self._params(from_date=from_date, to_date=to_date, status=status, search_text=search_text, count=count, start_position=start_position)\n"
        "            result = self.folders_api.list_items(account_id=self._account_id, folder_id=folder_id, **params)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List items (envelopes) in a specific folder.  [folders]",
    ),
]

# ---------- Account (Brands / Custom Fields) ----------
METHODS += [
    (
        # AccountsApi.list_brands(account_id)
        "list_brands(self) -> DocuSignResponse",
        "            result = self.accounts_api.list_brands(account_id=self._account_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List brands for the account.  [accounts]",
    ),
    (
        # AccountsApi.list_custom_fields(account_id)
        "list_custom_fields(self) -> DocuSignResponse",
        "            result = self.accounts_api.list_custom_fields(account_id=self._account_id)\n"
        "            return DocuSignResponse(success=True, data=result)",
        "List custom fields for the account.  [accounts]",
    ),
]


# -------------------------
# Code emission utilities
# -------------------------


def _emit_method(sig: str, body: str, doc: str) -> str:
    normalized_body = textwrap.indent(textwrap.dedent(body), "            ")
    return (
        f'    def {sig}:\n'
        f'        """{doc}"""\n'
        f'        try:\n'
        f'{normalized_body}\n'
        f'        except Exception as e:\n'
        f'            return DocuSignResponse(success=False, error=str(e), message="SDK call failed")\n'
    )


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

    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(code)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate DocuSignDataSource (docusign-esign SDK)."
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output path for the generated data source.",
    )
    parser.add_argument(
        "--response-import",
        default=DEFAULT_RESPONSE_IMPORT,
        help="Import line to bring in DocuSignResponse.",
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
    print(f"Generated DocuSignDataSource -> {args.out}  ({len(METHODS)} methods)")
    if args.do_print:
        print(code)


if __name__ == "__main__":
    main()
