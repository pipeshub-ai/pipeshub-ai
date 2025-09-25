#!/usr/bin/env python3
import argparse
import json
import logging
import re
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_SPEC_URL = "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json"
DEFAULT_OUT = "github_client.py"
DEFAULT_CLASS = "GitHubDataSource"


@dataclass
class Operation:
    op_id: str
    http_method: str
    path: str
    summary: str
    params: list[Mapping[str, Any]]

    @property
    def wrapper_name(self) -> str:
        return to_snake(self.op_id)


def read_bytes_from_url(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def load_spec(url: str = DEFAULT_SPEC_URL) -> Mapping[str, Any]:
    data = read_bytes_from_url(url)
    return json.loads(data.decode("utf-8"))


def extract_operations(spec: Mapping[str, Any]) -> list[Operation]:
    paths = spec.get("paths", {})
    ops: list[Operation] = []
    for path, item in paths.items():
        for method, op in item.items():
            if method.lower() not in {"get", "post", "put", "delete", "patch"}:
                continue
            op_id = (
                op.get("operationId") or f"{method}_{path.strip('/').replace('/', '_')}"
            )
            ops.append(
                Operation(
                    op_id=op_id,
                    http_method=method.upper(),
                    path=path,
                    summary=op.get("summary", ""),
                    params=op.get("parameters", []),
                ),
            )
    return sorted(ops, key=lambda o: o.op_id)


def to_snake(name: str) -> str:
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return (
        re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
        .replace(".", "_")
        .replace("/", "_")
        .replace("-", "_")
        .lower()
    )


def build_method_code(op: Operation) -> str:
    safe_op_id = op_id_safe(op.op_id)
    method_name = op.wrapper_name
    summary = op.summary or op.op_id
    http_method = op.http_method
    path = op.path
    original_op_id = op.op_id

    return f'''
    async def {method_name}(self, **kwargs) -> GitHubResponse:
        """{summary} (HTTP {http_method} {path})"""
        try:
            # Get the actual PyGithub instance
            github_sdk = self.client.get_sdk()
            # Map GitHub API operation IDs to PyGithub method names
            method_map = {{
                "users_get_authenticated": lambda sdk, **kw: sdk.get_user(),
                "users_get_by_username": lambda sdk, **kw: sdk.get_user(kw.get("username")),
                "repos_get": lambda sdk, **kw: sdk.get_repo(f"{{kw.get('owner')}}/{{kw.get('repo')}}"),
                "repos_list_for_authenticated_user": lambda sdk, **kw: sdk.get_user().get_repos(),
                "orgs_get": lambda sdk, **kw: sdk.get_organization(kw.get("org")),
                # Add more mappings as needed
            }}
            if "{safe_op_id}" in method_map:
                result = method_map["{safe_op_id}"](github_sdk, **kwargs)
                # Convert PyGithub objects to dict for serialization
                if hasattr(result, '_rawData'):
                    # Force PyGithub to load the data by accessing a property
                    if hasattr(result, 'login'):
                        _ = result.login  # This triggers data loading for User objects
                    elif hasattr(result, 'name'):
                        _ = result.name   # This triggers data loading for other objects
                    data = result._rawData
                elif hasattr(result, '__iter__') and not isinstance(result, (str, dict)):
                    # Handle paginated results (PaginatedList)
                    try:
                        # Convert to list and extract raw data
                        items = list(result)
                        data = []
                        for item in items:
                            if hasattr(item, '_rawData'):
                                if hasattr(item, 'name'):
                                    _ = item.name  # Trigger data loading
                                data.append(item._rawData)
                            else:
                                data.append(str(item))
                    except Exception as list_error:
                        data = f"Error processing paginated result: {{list_error}}"
                else:
                    data = str(result)
                return GitHubResponse(success=True, data=data)
            else:
                # Fallback: try to find method on SDK
                available = [attr for attr in dir(github_sdk) if not attr.startswith('_') and callable(getattr(github_sdk, attr))]
                return GitHubResponse(success=False, error=f"No mapping for '{original_op_id}' ({safe_op_id}). Available SDK methods: {{', '.join(available[:10])}}...")
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))'''


def op_id_safe(op_id: str) -> str:
    # Convert operation ID to valid Python identifier
    return re.sub(r"[^a-zA-Z0-9_]", "_", op_id)


def build_class_code(class_name: str, ops: Sequence[Operation]) -> str:
    methods = [build_method_code(o) for o in ops]
    header = f"""import logging
from typing import Any
from app.sources.client.github.github import GitHubClient, GitHubResponse
logger = logging.getLogger(__name__)
class {class_name}:
    def __init__(self, client: GitHubClient) -> None:
        self.client = client
"""
    return (
        header
        + "\n".join(methods)
        + f"\n\n__all__ = ['{class_name}', 'GitHubResponse']\n"
    )


def generate_github_client(
    out_path: str = DEFAULT_OUT, class_name: str = DEFAULT_CLASS,
) -> str:
    spec = load_spec()
    ops = extract_operations(spec)
    code = build_class_code(class_name, ops)

    # Create github directory if it doesn't exist
    github_dir = Path("github")
    github_dir.mkdir(exist_ok=True)

    # Write file inside github directory
    full_out_path = github_dir / out_path
    full_out_path.write_text(code, encoding="utf-8")
    return str(full_out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--class-name", default=DEFAULT_CLASS)
    ns = parser.parse_args()
    out = generate_github_client(ns.out, ns.class_name)
    print(f"✅ Generated {ns.class_name} -> {out}")
