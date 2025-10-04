# ruff: noqa
#!/usr/bin/env python3
"""
LinkedIn API — Code Generator

Generates a `LinkedInDataSource` class for LinkedIn's REST API operations.
This generator creates methods for:
- Profile and Organization data retrieval
- Posts and shares management
- Connection and network management
- Analytics and insights

The wrapper uses OAuth 2.0 authentication via access tokens.

Examples
--------
```python
from linkedin_codegen import generate_linkedin_client, import_generated
from app.sources.client.linkedin.linkedin import LinkedInClient

path = generate_linkedin_client(out_path="./linkedin_client.py")
LinkedInDataSource = import_generated(path, "LinkedInDataSource")

linkedin = LinkedInDataSource(LinkedInClient(access_token="your_access_token"))
response = linkedin.get_profile(user_id="~")
if response.success:
    print(f"Profile data: {response.data}")
else:
    print(f"Error: {response.error}")
```
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import keyword
import logging
import os
import re
import sys
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

# Set up logger
logger = logging.getLogger(__name__)

# ---- Defaults --------------------------------------------------------------
DEFAULT_OUT = "linkedin_client.py"
DEFAULT_CLASS = "LinkedInDataSource"

# ---- Operation Model -------------------------------------------------------
@dataclass
class Operation:
    op_id: str
    http_method: str
    path: str
    summary: str
    description: str
    params: List[Mapping[str, Any]]
    scopes: List[str]

    @property
    def wrapper_name(self) -> str:
        """Wrapper method name: snake_case."""
        return to_snake(self.op_id)


def to_snake(name: str) -> str:
    """Convert to snake_case."""
    n = name.replace(".", "_").replace("-", "_")
    out: List[str] = []
    for i, ch in enumerate(n):
        if ch.isupper():
            if i > 0 and (n[i-1].islower() or (i+1 < len(n) and n[i+1].islower())):
                out.append("_")
            out.append(ch.lower())
        else:
            out.append("_" if ch in "- " else ch)
    s = "".join(out)
    s = re.sub(r"__+", "_", s)
    return s.strip("_")


def sanitize_py_name(name: str) -> str:
    """Sanitize parameter names for Python."""
    n = re.sub(r"[^0-9a-zA-Z_]", "_", name)
    if n and n[0].isdigit():
        n = f"_{n}"
    if keyword.iskeyword(n):
        n += "_"
    if n.startswith("__"):
        n = f"_{n}"
    return n


def param_sig_fragment(p: Mapping[str, Any]) -> tuple[str, str, str]:
    """Return (py_name, api_name, default)."""
    api_name = str(p.get("name") or "param").strip()
    py_name = sanitize_py_name(api_name)
    default = "None" if not p.get("required") else "..."
    return py_name, api_name, default


def build_method_code(op: Operation) -> str:
    """Build Python method code for an operation."""
    required_parts: List[str] = []
    optional_parts: List[str] = []
    docs_param_lines: List[str] = []

    seen: set[str] = set()
    for p in op.params:
        api_name = str(p.get("name") or "param")
        if api_name in seen:
            continue
        seen.add(api_name)
        py_name, api_name, default = param_sig_fragment(p)
        if default == "...":
            required_parts.append(f"{py_name}: Any")
        else:
            optional_parts.append(f"{py_name}: Any = None")
        flag = "required" if default == "..." else "optional"
        desc = (p.get("description") or "").replace("\n", " ")
        docs_param_lines.append(f"            {py_name} ({flag}): {desc}")

    # Format signature
    if required_parts or optional_parts:
        star = ["*"] if (required_parts or optional_parts) else []
        all_params = star + required_parts + optional_parts + ["**kwargs"]
        if len(all_params) <= 3:
            sig = ", ".join(all_params)
        else:
            params_formatted = ",\n        ".join(all_params)
            sig = f"\n        {params_formatted}\n    "
    else:
        sig = "**kwargs"

    summary = op.summary or op.op_id
    params_doc = "\n".join(docs_param_lines) if docs_param_lines else "            (no parameters)"
    scopes_doc = ", ".join(op.scopes) if op.scopes else "Not specified"

    # Build parameter mapping
    param_mapping = []
    for p in op.params:
        api_name = str(p.get("name") or "param")
        py_name, api_name, default = param_sig_fragment(p)
        param_mapping.append(f"        if {py_name} is not None:\n            kwargs_api['{api_name}'] = {py_name}")

    param_mapping_code = "\n".join(param_mapping) if param_mapping else "        # No parameters"

    method_code = f'''    async def {op.wrapper_name}(self, {sig}) -> LinkedInResponse:
        """{summary}

        LinkedIn method: `{op.op_id}` (HTTP {op.http_method} {op.path})
        Required scopes: {scopes_doc}

        Args:
{params_doc}

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {{}}
{param_mapping_code}
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='{op.http_method}',
                path='{op.path}',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)
'''

    return method_code


def build_class_code(class_name: str, ops: Sequence[Operation]) -> str:
    """Build the complete class code."""
    methods = [build_method_code(o) for o in ops]

    header = f'''import logging
from typing import Any, Dict
from app.sources.client.linkedin.linkedin import LinkedInClient, LinkedInResponse

# Set up logger
logger = logging.getLogger(__name__)

class {class_name}:
    """Auto-generated LinkedIn REST API client wrapper.
    - Uses OAuth 2.0 authentication
    - Snake_case method names
    - All responses wrapped in standardized LinkedInResponse format
    """
    def __init__(self, client: LinkedInClient) -> None:
        self.client = client

'''

    runtime_helpers = """
    async def _handle_linkedin_response(self, response: Any) -> LinkedInResponse:  # noqa: ANN401
        \"\"\"Handle LinkedIn API response and convert to standardized format\"\"\"
        try:
            if not response:
                return LinkedInResponse(success=False, error="Empty response from LinkedIn API")

            # LinkedIn responses are typically JSON
            if isinstance(response, dict):
                data = response
                success = True
                error_msg = None

                # Check for error indicators
                if 'error' in data:
                    success = False
                    error_msg = data.get('message') or str(data.get('error'))
                elif 'status' in data and data['status'] >= 400:
                    success = False
                    error_msg = data.get('message') or f"HTTP {data['status']}"

                return LinkedInResponse(
                    success=success,
                    data=data,
                    error=error_msg
                )
            else:
                return LinkedInResponse(
                    success=True,
                    data={"raw_response": str(response)}
                )
        except Exception as e:
            logger.error(f"Error handling LinkedIn response: {e}")
            return LinkedInResponse(success=False, error=str(e))

    async def _handle_linkedin_error(self, error: Exception) -> LinkedInResponse:
        \"\"\"Handle LinkedIn API errors and convert to standardized format\"\"\"
        error_msg = str(error)
        logger.error(f"LinkedIn API error: {error_msg}")
        return LinkedInResponse(success=False, error=error_msg)

"""

    return header + runtime_helpers + "\n" + "\n".join(methods) + f"\n\n__all__ = ['{class_name}', 'LinkedInResponse']\n"


def generate_linkedin_operations() -> List[Operation]:
    """Generate LinkedIn API operations based on LinkedIn REST API v2."""
    operations = [
        # Profile Operations
        Operation(
            op_id="get_profile",
            http_method="GET",
            path="/me",
            summary="Get current user's profile",
            description="Retrieve the authenticated user's LinkedIn profile information",
            params=[],
            scopes=["r_liteprofile", "r_basicprofile"]
        ),
        Operation(
            op_id="get_profile_by_id",
            http_method="GET",
            path="/people/{id}",
            summary="Get profile by ID",
            description="Retrieve a specific user's profile by their ID",
            params=[
                {"name": "id", "required": True, "description": "LinkedIn member ID or 'me' for current user"}
            ],
            scopes=["r_liteprofile"]
        ),
        
        # Organization Operations
        Operation(
            op_id="get_organization",
            http_method="GET",
            path="/organizations/{id}",
            summary="Get organization details",
            description="Retrieve details about a specific organization",
            params=[
                {"name": "id", "required": True, "description": "Organization ID"}
            ],
            scopes=["r_organization_social"]
        ),
        Operation(
            op_id="get_organization_brand",
            http_method="GET",
            path="/organizationBrands/{id}",
            summary="Get organization brand",
            description="Retrieve organization brand information",
            params=[
                {"name": "id", "required": True, "description": "Organization brand ID"}
            ],
            scopes=["r_organization_social"]
        ),
        
        # Post/Share Operations
        Operation(
            op_id="create_share",
            http_method="POST",
            path="/ugcPosts",
            summary="Create a post/share",
            description="Create a new post or share content on LinkedIn",
            params=[
                {"name": "author", "required": True, "description": "URN of the author (person or organization)"},
                {"name": "lifecycleState", "required": False, "description": "Lifecycle state of the share"},
                {"name": "specificContent", "required": True, "description": "Content specific to the share"},
                {"name": "visibility", "required": True, "description": "Visibility settings for the share"}
            ],
            scopes=["w_member_social"]
        ),
        Operation(
            op_id="get_shares",
            http_method="GET",
            path="/ugcPosts",
            summary="Get shares",
            description="Retrieve shares/posts",
            params=[
                {"name": "q", "required": True, "description": "Query parameter (e.g., 'authors')"},
                {"name": "authors", "required": False, "description": "List of author URNs"},
                {"name": "count", "required": False, "description": "Number of results to return"},
                {"name": "start", "required": False, "description": "Starting position"}
            ],
            scopes=["r_member_social"]
        ),
        Operation(
            op_id="get_share_statistics",
            http_method="GET",
            path="/organizationalEntityShareStatistics",
            summary="Get share statistics",
            description="Retrieve statistics for organizational shares",
            params=[
                {"name": "q", "required": True, "description": "Query type (e.g., 'organizationalEntity')"},
                {"name": "organizationalEntity", "required": True, "description": "Organization URN"}
            ],
            scopes=["r_organization_social"]
        ),
        
        # Connection Operations  
        Operation(
            op_id="get_connections",
            http_method="GET",
            path="/connections",
            summary="Get connections",
            description="Retrieve user's connections",
            params=[
                {"name": "q", "required": False, "description": "Query parameter"},
                {"name": "start", "required": False, "description": "Starting position"},
                {"name": "count", "required": False, "description": "Number of results"}
            ],
            scopes=["r_basicprofile"]
        ),
        
        # Analytics Operations
        Operation(
            op_id="get_organization_follower_statistics",
            http_method="GET",
            path="/organizationalEntityFollowerStatistics",
            summary="Get follower statistics",
            description="Retrieve follower statistics for an organization",
            params=[
                {"name": "q", "required": True, "description": "Query type"},
                {"name": "organizationalEntity", "required": True, "description": "Organization URN"}
            ],
            scopes=["r_organization_social"]
        ),
        Operation(
            op_id="get_page_statistics",
            http_method="GET",
            path="/organizationPageStatistics/{organization}",
            summary="Get page statistics",
            description="Retrieve page statistics for an organization",
            params=[
                {"name": "organization", "required": True, "description": "Organization ID"}
            ],
            scopes=["r_organization_social"]
        ),
        
        # Search Operations
        Operation(
            op_id="search_companies",
            http_method="GET",
            path="/search/companies",
            summary="Search companies",
            description="Search for companies on LinkedIn",
            params=[
                {"name": "q", "required": True, "description": "Search query"},
                {"name": "start", "required": False, "description": "Starting position"},
                {"name": "count", "required": False, "description": "Number of results"}
            ],
            scopes=["r_basicprofile"]
        ),
        
        # Member/People Operations
        Operation(
            op_id="get_member_companies",
            http_method="GET",
            path="/people/{id}/network/company-updates",
            summary="Get member company updates",
            description="Retrieve company updates from a member's network",
            params=[
                {"name": "id", "required": True, "description": "Member ID"},
                {"name": "count", "required": False, "description": "Number of results"},
                {"name": "start", "required": False, "description": "Starting position"}
            ],
            scopes=["r_network"]
        ),
        
        # Comment Operations
        Operation(
            op_id="create_comment",
            http_method="POST",
            path="/socialActions/{shareUrn}/comments",
            summary="Create a comment",
            description="Create a comment on a post/share",
            params=[
                {"name": "shareUrn", "required": True, "description": "URN of the share to comment on"},
                {"name": "message", "required": True, "description": "Comment message"}
            ],
            scopes=["w_member_social"]
        ),
        Operation(
            op_id="get_comments",
            http_method="GET",
            path="/socialActions/{shareUrn}/comments",
            summary="Get comments",
            description="Retrieve comments on a post/share",
            params=[
                {"name": "shareUrn", "required": True, "description": "URN of the share"},
                {"name": "count", "required": False, "description": "Number of results"},
                {"name": "start", "required": False, "description": "Starting position"}
            ],
            scopes=["r_member_social"]
        ),
        
        # Like/Reaction Operations
        Operation(
            op_id="create_like",
            http_method="POST",
            path="/socialActions/{shareUrn}/likes",
            summary="Like a post",
            description="Like/react to a post or share",
            params=[
                {"name": "shareUrn", "required": True, "description": "URN of the share to like"}
            ],
            scopes=["w_member_social"]
        ),
        Operation(
            op_id="delete_like",
            http_method="DELETE",
            path="/socialActions/{shareUrn}/likes/{actor}",
            summary="Unlike a post",
            description="Remove like/reaction from a post",
            params=[
                {"name": "shareUrn", "required": True, "description": "URN of the share"},
                {"name": "actor", "required": True, "description": "Actor URN (person or organization)"}
            ],
            scopes=["w_member_social"]
        ),
    ]
    
    return operations


def generate_linkedin_client(
    *,
    out_path: str = DEFAULT_OUT,
    class_name: str = DEFAULT_CLASS,
) -> str:
    """Generate the LinkedIn client wrapper Python file. Returns its path."""
    ops = generate_linkedin_operations()
    code = build_class_code(class_name, ops)
    
    # Create linkedin directory
    script_dir = Path(__file__).parent if __file__ else Path('.')
    linkedin_dir = script_dir.parent / 'app' / 'sources' / 'external' / 'linkedin'
    linkedin_dir.mkdir(parents=True, exist_ok=True)
    
    # Set the full file path
    full_path = linkedin_dir / out_path
    full_path.write_text(code, encoding="utf-8")
    return str(full_path)


def import_generated(path: str, symbol: str = DEFAULT_CLASS):
    """Import the generated module and return a symbol."""
    module_name = Path(path).stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return getattr(module, symbol)


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate LinkedIn REST API client")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Output .py file path")
    ap.add_argument("--class-name", default=DEFAULT_CLASS, help="Generated class name")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    ns = _parse_args(argv)
    out_path = generate_linkedin_client(out_path=ns.out, class_name=ns.class_name)
    ops = generate_linkedin_operations()
    print(f"✅ Generated {ns.class_name} -> {out_path}")
    print(f"📁 Files saved in: {Path(out_path).parent}")
    print(f"📊 Generated {len(ops)} methods for LinkedIn REST API")


if __name__ == "__main__":
    main()
