# ruff: noqa
"""
DokuWiki JSON-RPC API Code Generator

Generates DokuWikiDataSource class covering DokuWiki JSON API:
- Info (API version, wiki time, title, version)
- Pages (get, HTML, info, history, backlinks, links, recent changes, list, save, append, search, lock, unlock)
- Media (get, info, history, usage, recent changes, list, save, delete)
- User (ACL check, login, logoff, whoami)
- Plugins - ACL (add, delete, list)
- Plugins - Extensions (list, search, install, uninstall, enable, disable)
- Plugins - User Manager (create user, delete user)
- Plugins - Config Manager (get configs)

The generated DataSource accepts a DokuWikiClient and uses the client's
base URL (https://<instance>/lib/exe/jsonapi.php) to construct request URLs.
The method name is appended as a path (e.g., /core.getPage).

All methods are POST. All parameters are in the request body.
All methods have explicit parameter signatures with no **kwargs usage.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional


# ================================================================================
# DokuWiki JSON API Endpoints - organized by resource
#
# Each endpoint defines:
#   method: HTTP verb (always POST for DokuWiki JSON API)
#   path: Method name path (appended to base_url)
#   description: Human-readable description
#   parameters: Dict of param_name -> {type, location (always body), description}
#               Optional: api_name (the actual API parameter name if different from Python name)
#   required: List of required parameter names
# ================================================================================

DOKUWIKI_API_ENDPOINTS = {
    # ================================================================================
    # INFO
    # ================================================================================
    "get_api_version": {
        "method": "POST",
        "path": "/core.getAPIVersion",
        "description": "Return the API version",
        "parameters": {},
        "required": [],
    },
    "get_wiki_time": {
        "method": "POST",
        "path": "/core.getWikiTime",
        "description": "Return the current server time",
        "parameters": {},
        "required": [],
    },
    "get_wiki_title": {
        "method": "POST",
        "path": "/core.getWikiTitle",
        "description": "Returns the wiki title",
        "parameters": {},
        "required": [],
    },
    "get_wiki_version": {
        "method": "POST",
        "path": "/core.getWikiVersion",
        "description": "Return DokuWiki's version",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # PAGES
    # ================================================================================
    "get_page": {
        "method": "POST",
        "path": "/core.getPage",
        "description": "Get a wiki page's syntax",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Wiki page id"},
            "rev": {"type": "Optional[int]", "location": "body", "description": "Revision timestamp to access an older revision"},
        },
        "required": ["page"],
    },
    "get_page_html": {
        "method": "POST",
        "path": "/core.getPageHTML",
        "description": "Return a wiki page rendered to HTML",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id"},
            "rev": {"type": "Optional[int]", "location": "body", "description": "Revision timestamp"},
        },
        "required": ["page"],
    },
    "get_page_info": {
        "method": "POST",
        "path": "/core.getPageInfo",
        "description": "Return some basic data about a page",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id"},
            "rev": {"type": "Optional[int]", "location": "body", "description": "Revision timestamp"},
            "author": {"type": "Optional[bool]", "location": "body", "description": "Include author info"},
            "hash": {"type": "Optional[bool]", "location": "body", "description": "Include content hash"},
        },
        "required": ["page"],
    },
    "get_page_history": {
        "method": "POST",
        "path": "/core.getPageHistory",
        "description": "Returns revisions of a wiki page",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id"},
            "first": {"type": "Optional[int]", "location": "body", "description": "Skip the first n results"},
        },
        "required": ["page"],
    },
    "get_page_back_links": {
        "method": "POST",
        "path": "/core.getPageBackLinks",
        "description": "Get a page's backlinks",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id"},
        },
        "required": ["page"],
    },
    "get_page_links": {
        "method": "POST",
        "path": "/core.getPageLinks",
        "description": "Get a page's links",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id"},
        },
        "required": ["page"],
    },
    "get_recent_page_changes": {
        "method": "POST",
        "path": "/core.getRecentPageChanges",
        "description": "Get recent page changes",
        "parameters": {
            "timestamp": {"type": "Optional[int]", "location": "body", "description": "Unix timestamp to get changes since"},
        },
        "required": [],
    },
    "list_pages": {
        "method": "POST",
        "path": "/core.listPages",
        "description": "List all pages in namespace",
        "parameters": {
            "namespace": {"type": "Optional[str]", "location": "body", "description": "Namespace to list pages from"},
            "depth": {"type": "Optional[int]", "location": "body", "description": "Depth of namespace traversal"},
            "hash": {"type": "Optional[bool]", "location": "body", "description": "Include content hash"},
        },
        "required": [],
    },
    "save_page": {
        "method": "POST",
        "path": "/core.savePage",
        "description": "Save a wiki page",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id"},
            "text": {"type": "str", "location": "body", "description": "Page content"},
            "summary": {"type": "Optional[str]", "location": "body", "description": "Edit summary"},
            "isminor": {"type": "Optional[bool]", "location": "body", "description": "Mark as minor edit"},
        },
        "required": ["page", "text"],
    },
    "append_page": {
        "method": "POST",
        "path": "/core.appendPage",
        "description": "Append text to a wiki page",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id"},
            "text": {"type": "str", "location": "body", "description": "Text to append"},
            "summary": {"type": "Optional[str]", "location": "body", "description": "Edit summary"},
            "isminor": {"type": "Optional[bool]", "location": "body", "description": "Mark as minor edit"},
        },
        "required": ["page", "text"],
    },
    "search_pages": {
        "method": "POST",
        "path": "/core.searchPages",
        "description": "Full text search",
        "parameters": {
            "query": {"type": "str", "location": "body", "description": "Search query string"},
        },
        "required": ["query"],
    },
    "lock_pages": {
        "method": "POST",
        "path": "/core.lockPages",
        "description": "Lock pages",
        "parameters": {
            "pages": {"type": "list[Any]", "location": "body", "description": "List of page ids to lock"},
        },
        "required": ["pages"],
    },
    "unlock_pages": {
        "method": "POST",
        "path": "/core.unlockPages",
        "description": "Unlock pages",
        "parameters": {
            "pages": {"type": "list[Any]", "location": "body", "description": "List of page ids to unlock"},
        },
        "required": ["pages"],
    },

    # ================================================================================
    # MEDIA
    # ================================================================================
    "get_media": {
        "method": "POST",
        "path": "/core.getMedia",
        "description": "Get a media file's content",
        "parameters": {
            "media": {"type": "str", "location": "body", "description": "Media file id"},
            "rev": {"type": "Optional[int]", "location": "body", "description": "Revision timestamp"},
        },
        "required": ["media"],
    },
    "get_media_info": {
        "method": "POST",
        "path": "/core.getMediaInfo",
        "description": "Return info about a media file",
        "parameters": {
            "media": {"type": "str", "location": "body", "description": "Media file id"},
            "rev": {"type": "Optional[int]", "location": "body", "description": "Revision timestamp"},
            "author": {"type": "Optional[bool]", "location": "body", "description": "Include author info"},
            "hash": {"type": "Optional[bool]", "location": "body", "description": "Include content hash"},
        },
        "required": ["media"],
    },
    "get_media_history": {
        "method": "POST",
        "path": "/core.getMediaHistory",
        "description": "Get media revisions",
        "parameters": {
            "media": {"type": "str", "location": "body", "description": "Media file id"},
            "first": {"type": "Optional[int]", "location": "body", "description": "Skip the first n results"},
        },
        "required": ["media"],
    },
    "get_media_usage": {
        "method": "POST",
        "path": "/core.getMediaUsage",
        "description": "Get pages using a media file",
        "parameters": {
            "media": {"type": "str", "location": "body", "description": "Media file id"},
        },
        "required": ["media"],
    },
    "get_recent_media_changes": {
        "method": "POST",
        "path": "/core.getRecentMediaChanges",
        "description": "Get recent media changes",
        "parameters": {
            "timestamp": {"type": "Optional[int]", "location": "body", "description": "Unix timestamp to get changes since"},
        },
        "required": [],
    },
    "list_media": {
        "method": "POST",
        "path": "/core.listMedia",
        "description": "List media files in namespace",
        "parameters": {
            "namespace": {"type": "Optional[str]", "location": "body", "description": "Namespace to list media from"},
            "depth": {"type": "Optional[int]", "location": "body", "description": "Depth of namespace traversal"},
            "hash": {"type": "Optional[bool]", "location": "body", "description": "Include content hash"},
            "pattern": {"type": "Optional[str]", "location": "body", "description": "File name pattern filter"},
        },
        "required": [],
    },
    "save_media": {
        "method": "POST",
        "path": "/core.saveMedia",
        "description": "Upload a file",
        "parameters": {
            "media": {"type": "str", "location": "body", "description": "Media file id"},
            "base64": {"type": "str", "location": "body", "description": "Base64 encoded file content"},
            "overwrite": {"type": "Optional[bool]", "location": "body", "description": "Overwrite existing file"},
        },
        "required": ["media", "base64"],
    },
    "delete_media": {
        "method": "POST",
        "path": "/core.deleteMedia",
        "description": "Delete a file",
        "parameters": {
            "media": {"type": "str", "location": "body", "description": "Media file id"},
        },
        "required": ["media"],
    },

    # ================================================================================
    # USER
    # ================================================================================
    "acl_check": {
        "method": "POST",
        "path": "/core.aclCheck",
        "description": "Check ACL Permissions",
        "parameters": {
            "page": {"type": "str", "location": "body", "description": "Page id to check permissions for"},
            "user": {"type": "Optional[str]", "location": "body", "description": "Username to check permissions for"},
            "groups": {"type": "Optional[list[Any]]", "location": "body", "description": "Groups to check permissions for"},
        },
        "required": ["page"],
    },
    "login": {
        "method": "POST",
        "path": "/core.login",
        "description": "Login",
        "parameters": {
            "user": {"type": "str", "location": "body", "description": "Username"},
            "password": {"type": "str", "location": "body", "description": "Password", "api_name": "pass"},
        },
        "required": ["user", "password"],
    },
    "logoff": {
        "method": "POST",
        "path": "/core.logoff",
        "description": "Log off",
        "parameters": {},
        "required": [],
    },
    "who_am_i": {
        "method": "POST",
        "path": "/core.whoAmI",
        "description": "Info about current user",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # PLUGINS - ACL
    # ================================================================================
    "add_acl": {
        "method": "POST",
        "path": "/plugin.acl.addAcl",
        "description": "Add ACL rule",
        "parameters": {
            "scope": {"type": "str", "location": "body", "description": "ACL scope"},
            "user": {"type": "str", "location": "body", "description": "User or group name"},
            "level": {"type": "int", "location": "body", "description": "Permission level"},
        },
        "required": ["scope", "user", "level"],
    },
    "delete_acl": {
        "method": "POST",
        "path": "/plugin.acl.delAcl",
        "description": "Remove ACL entry",
        "parameters": {
            "scope": {"type": "str", "location": "body", "description": "ACL scope"},
            "user": {"type": "str", "location": "body", "description": "User or group name"},
        },
        "required": ["scope", "user"],
    },
    "list_acls": {
        "method": "POST",
        "path": "/plugin.acl.listAcls",
        "description": "List ACL entries",
        "parameters": {},
        "required": [],
    },

    # ================================================================================
    # PLUGINS - EXTENSIONS
    # ================================================================================
    "list_extensions": {
        "method": "POST",
        "path": "/plugin.extension.list",
        "description": "List installed extensions",
        "parameters": {},
        "required": [],
    },
    "search_extensions": {
        "method": "POST",
        "path": "/plugin.extension.search",
        "description": "Search extensions",
        "parameters": {
            "query": {"type": "str", "location": "body", "description": "Search query"},
            "max": {"type": "Optional[int]", "location": "body", "description": "Maximum number of results"},
        },
        "required": ["query"],
    },
    "install_extension": {
        "method": "POST",
        "path": "/plugin.extension.install",
        "description": "Install extension",
        "parameters": {
            "extension": {"type": "str", "location": "body", "description": "Extension identifier"},
        },
        "required": ["extension"],
    },
    "uninstall_extension": {
        "method": "POST",
        "path": "/plugin.extension.uninstall",
        "description": "Uninstall extension",
        "parameters": {
            "extension": {"type": "str", "location": "body", "description": "Extension identifier"},
        },
        "required": ["extension"],
    },
    "enable_extension": {
        "method": "POST",
        "path": "/plugin.extension.enable",
        "description": "Enable extension",
        "parameters": {
            "extension": {"type": "str", "location": "body", "description": "Extension identifier"},
        },
        "required": ["extension"],
    },
    "disable_extension": {
        "method": "POST",
        "path": "/plugin.extension.disable",
        "description": "Disable extension",
        "parameters": {
            "extension": {"type": "str", "location": "body", "description": "Extension identifier"},
        },
        "required": ["extension"],
    },

    # ================================================================================
    # PLUGINS - USER MANAGER
    # ================================================================================
    "create_user": {
        "method": "POST",
        "path": "/plugin.usermanager.createUser",
        "description": "Create a new user",
        "parameters": {
            "user": {"type": "str", "location": "body", "description": "Login name"},
            "name": {"type": "str", "location": "body", "description": "Full name"},
            "mail": {"type": "str", "location": "body", "description": "Email address"},
            "groups": {"type": "list[Any]", "location": "body", "description": "List of group names"},
            "password": {"type": "Optional[str]", "location": "body", "description": "Password (auto-generated if omitted)"},
            "notify": {"type": "Optional[bool]", "location": "body", "description": "Send notification to user"},
        },
        "required": ["user", "name", "mail", "groups"],
    },
    "delete_user": {
        "method": "POST",
        "path": "/plugin.usermanager.deleteUser",
        "description": "Remove a user",
        "parameters": {
            "user": {"type": "list[Any]", "location": "body", "description": "List of login names to delete"},
        },
        "required": ["user"],
    },

    # ================================================================================
    # PLUGINS - CONFIG MANAGER
    # ================================================================================
    "get_configs": {
        "method": "POST",
        "path": "/plugin.confmanager.getConfigs",
        "description": "Get configuration",
        "parameters": {},
        "required": [],
    },
}


class DokuWikiDataSourceGenerator:
    """Generator for comprehensive DokuWiki JSON API datasource class.

    Generates methods for DokuWiki JSON API endpoints.
    The generated DataSource class accepts a DokuWikiClient whose base URL
    setting determines the API endpoint (https://<instance>/lib/exe/jsonapi.php).

    All methods have explicit parameter signatures.
    All methods are POST (DokuWiki JSON API convention).
    """

    def __init__(self):
        self.generated_methods: List[Dict[str, str]] = []

    def _sanitize_parameter_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        sanitized = name.replace("-", "_").replace(".", "_").replace("/", "_")
        if sanitized and not (sanitized[0].isalpha() or sanitized[0] == "_"):
            sanitized = f"param_{sanitized}"
        # Avoid shadowing Python builtins
        if sanitized == "format":
            sanitized = "image_format"
        return sanitized

    def _build_request_body(self, endpoint_info: Dict) -> List[str]:
        """Build request body handling."""
        body_params = {
            name: info
            for name, info in endpoint_info["parameters"].items()
            if info["location"] == "body"
        }

        if not body_params:
            return []

        lines = ["        body: dict[str, Any] = {}"]

        for param_name, param_info in body_params.items():
            sanitized_name = self._sanitize_parameter_name(param_name)
            api_name = param_info.get("api_name", param_name)

            if param_name in endpoint_info["required"]:
                lines.append(f"        body['{api_name}'] = {sanitized_name}")
            else:
                lines.extend([
                    f"        if {sanitized_name} is not None:",
                    f"            body['{api_name}'] = {sanitized_name}",
                ])

        return lines

    @staticmethod
    def _modernize_type(type_str: str) -> str:
        """Convert typing-style annotations to modern Python 3.10+ syntax.

        Optional[str] -> str | None, Dict[str, Any] -> dict[str, Any],
        List[str] -> list[str], etc.
        """
        if type_str.startswith("Optional[") and type_str.endswith("]"):
            inner = type_str[len("Optional["):-1]
            inner = DokuWikiDataSourceGenerator._modernize_type(inner)
            return f"{inner} | None"
        if type_str.startswith("Dict["):
            inner = type_str[len("Dict["):-1]
            parts = DokuWikiDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                DokuWikiDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"dict[{modernized}]"
        if type_str == "Dict":
            return "dict"
        if type_str.startswith("List["):
            inner = type_str[len("List["):-1]
            parts = DokuWikiDataSourceGenerator._split_type_args(inner)
            modernized = ", ".join(
                DokuWikiDataSourceGenerator._modernize_type(p.strip()) for p in parts
            )
            return f"list[{modernized}]"
        if type_str == "List":
            return "list"
        return type_str

    @staticmethod
    def _split_type_args(s: str) -> List[str]:
        """Split type arguments respecting nested brackets."""
        parts = []
        depth = 0
        current = ""
        for ch in s:
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
            if ch == "," and depth == 0:
                parts.append(current.strip())
                current = ""
            else:
                current += ch
        if current.strip():
            parts.append(current.strip())
        return parts

    def _generate_method_signature(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate method signature with explicit parameters."""
        params = ["self"]
        has_any_bool = False

        # Collect required params, split into non-bool and bool groups
        required_non_bool: List[str] = []
        required_bool: List[str] = []
        for param_name in endpoint_info["required"]:
            if param_name in endpoint_info["parameters"]:
                param_info = endpoint_info["parameters"][param_name]
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                param_str = f"{sanitized_name}: {modern_type}"
                if "bool" in param_info.get("type", ""):
                    required_bool.append(param_str)
                    has_any_bool = True
                else:
                    required_non_bool.append(param_str)

        # Collect optional parameters
        optional_params: List[str] = []
        for param_name, param_info in endpoint_info["parameters"].items():
            if param_name not in endpoint_info["required"]:
                sanitized_name = self._sanitize_parameter_name(param_name)
                modern_type = self._modernize_type(param_info["type"])
                if "| None" not in modern_type:
                    modern_type = f"{modern_type} | None"
                optional_params.append(f"{sanitized_name}: {modern_type} = None")
                if "bool" in param_info.get("type", ""):
                    has_any_bool = True

        # Build signature: non-bool required first, then * if needed, then bool required + optional
        params.extend(required_non_bool)
        if has_any_bool and (required_bool or optional_params):
            params.append("*")
        params.extend(required_bool)
        params.extend(optional_params)

        signature_params = ",\n        ".join(params)
        return f"    async def {method_name}(\n        {signature_params}\n    ) -> DokuWikiResponse:"

    def _generate_method_docstring(self, endpoint_info: Dict) -> List[str]:
        """Generate method docstring."""
        lines = [f'        """{endpoint_info["description"]}', ""]

        if endpoint_info["parameters"]:
            lines.append("        Args:")
            for param_name, param_info in endpoint_info["parameters"].items():
                sanitized_name = self._sanitize_parameter_name(param_name)
                lines.append(
                    f"            {sanitized_name}: {param_info['description']}"
                )
            lines.append("")

        lines.extend([
            "        Returns:",
            "            DokuWikiResponse with operation result",
            '        """',
        ])

        return lines

    def _generate_method(self, method_name: str, endpoint_info: Dict) -> str:
        """Generate a complete method for an API endpoint."""
        lines = []

        # Method signature
        lines.append(self._generate_method_signature(method_name, endpoint_info))

        # Docstring
        lines.extend(self._generate_method_docstring(endpoint_info))

        # URL construction (no path params in DokuWiki - always static paths)
        lines.append(f'        url = self.base_url + "{endpoint_info["path"]}"')

        # Request body
        body_lines = self._build_request_body(endpoint_info)
        if body_lines:
            lines.append("")
            lines.extend(body_lines)

        # Request construction and execution
        lines.append("")
        lines.append("        try:")
        lines.append("            request = HTTPRequest(")
        lines.append(f'                method="{endpoint_info["method"]}",')
        lines.append("                url=url,")
        lines.append('                headers={"Content-Type": "application/json"},')
        if body_lines:
            lines.append("                body=body,")
        lines.append("            )")
        lines.extend([
            "            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]",
            "            response_data = response.json() if response.text() else None",
            "            return DokuWikiResponse(",
            "                success=response.status < HTTP_ERROR_THRESHOLD,",
            "                data=response_data,",
            f'                message="Successfully executed {method_name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"',
            "            )",
            "        except Exception as e:",
            f'            return DokuWikiResponse(success=False, error=str(e), message="Failed to execute {method_name}")',
        ])

        self.generated_methods.append({
            "name": method_name,
            "endpoint": endpoint_info["path"],
            "method": endpoint_info["method"],
            "description": endpoint_info["description"],
        })

        return "\n".join(lines)

    def generate_dokuwiki_datasource(self) -> str:
        """Generate the complete DokuWiki datasource class."""

        class_lines = [
            "# ruff: noqa",
            '"""',
            "DokuWiki JSON API DataSource - Auto-generated API wrapper",
            "",
            "Generated from DokuWiki JSON API documentation.",
            "Uses HTTP client for direct REST API interactions.",
            "All methods are POST (DokuWiki JSON-RPC convention).",
            "All methods have explicit parameter signatures.",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "",
            "from app.sources.client.dokuwiki.dokuwiki import DokuWikiClient, DokuWikiResponse",
            "from app.sources.client.http.http_request import HTTPRequest",
            "",
            "# HTTP status code constant",
            "HTTP_ERROR_THRESHOLD = 400",
            "",
            "",
            "class DokuWikiDataSource:",
            '    """DokuWiki JSON API DataSource',
            "",
            "    Provides async wrapper methods for DokuWiki JSON API operations:",
            "    - Info (API version, wiki time, title, version)",
            "    - Pages (get, HTML, info, history, backlinks, links, recent changes, list, save, append, search, lock, unlock)",
            "    - Media (get, info, history, usage, recent changes, list, save, delete)",
            "    - User (ACL check, login, logoff, whoami)",
            "    - Plugins - ACL (add, delete, list)",
            "    - Plugins - Extensions (list, search, install, uninstall, enable, disable)",
            "    - Plugins - User Manager (create user, delete user)",
            "    - Plugins - Config Manager (get configs)",
            "",
            "    The base URL is determined by the DokuWikiClient's configured base URL",
            "    (default: https://<instance>/lib/exe/jsonapi.php).",
            "",
            "    All methods return DokuWikiResponse objects.",
            "    All methods are POST requests.",
            '    """',
            "",
            "    def __init__(self, client: DokuWikiClient) -> None:",
            '        """Initialize with DokuWikiClient.',
            "",
            "        Args:",
            "            client: DokuWikiClient instance with configured authentication",
            '        """',
            "        self._client = client",
            "        self.http = client.get_client()",
            "        try:",
            "            self.base_url = self.http.get_base_url().rstrip('/')",
            "        except AttributeError as exc:",
            "            raise ValueError('HTTP client does not have get_base_url method') from exc",
            "",
            "    def get_data_source(self) -> 'DokuWikiDataSource':",
            '        """Return the data source instance."""',
            "        return self",
            "",
            "    def get_client(self) -> DokuWikiClient:",
            '        """Return the underlying DokuWikiClient."""',
            "        return self._client",
            "",
        ]

        # Generate all API methods
        for method_name, endpoint_info in DOKUWIKI_API_ENDPOINTS.items():
            class_lines.append(self._generate_method(method_name, endpoint_info))
            class_lines.append("")

        return "\n".join(class_lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the DokuWiki datasource to a file."""
        if filename is None:
            filename = "dokuwiki.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        dokuwiki_dir = script_dir.parent / "app" / "sources" / "external" / "dokuwiki"
        dokuwiki_dir.mkdir(parents=True, exist_ok=True)

        full_path = dokuwiki_dir / filename

        class_code = self.generate_dokuwiki_datasource()

        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated DokuWiki data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary by category
        resource_categories = {
            "Info": 0,
            "Pages": 0,
            "Media": 0,
            "User": 0,
            "Plugins - ACL": 0,
            "Plugins - Extensions": 0,
            "Plugins - User Manager": 0,
            "Plugins - Config Manager": 0,
        }

        for method in self.generated_methods:
            name = method["name"]
            endpoint = method["endpoint"]
            if "plugin.acl" in endpoint:
                resource_categories["Plugins - ACL"] += 1
            elif "plugin.extension" in endpoint:
                resource_categories["Plugins - Extensions"] += 1
            elif "plugin.usermanager" in endpoint:
                resource_categories["Plugins - User Manager"] += 1
            elif "plugin.confmanager" in endpoint:
                resource_categories["Plugins - Config Manager"] += 1
            elif any(x in name for x in ["get_api_version", "get_wiki_time", "get_wiki_title", "get_wiki_version"]):
                resource_categories["Info"] += 1
            elif any(x in name for x in ["page", "search_pages", "lock_pages", "unlock_pages", "list_pages"]):
                resource_categories["Pages"] += 1
            elif "media" in name:
                resource_categories["Media"] += 1
            elif any(x in name for x in ["acl_check", "login", "logoff", "who_am_i"]):
                resource_categories["User"] += 1

        print(f"\nMethods by Resource:")
        for category, count in resource_categories.items():
            if count > 0:
                print(f"  - {category}: {count}")


def main():
    """Main function for DokuWiki data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate DokuWiki JSON API data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = DokuWikiDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate DokuWiki data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
