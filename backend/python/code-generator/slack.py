# ruff: noqa
#!/usr/bin/env python3
"""
Slack Web API — Code Generator (strict, no fallback)

Generates a `SlackDataSource` class at build-time from Slack's OpenAPI (Swagger)
so that **every Web API operation** becomes a Python method. Requirements:

- **Method names** in the generated class are **fully snake_case**
  (e.g., `chat.postMessage` → `chat_post_message`,
   `admin.conversations.convertToPrivate` → `admin_conversations_convert_to_private`).
- The wrapper **only** uses the official SDK method aliases on the provided
  client (constructor accepts **SlackClient**). **No `api_call` fallback.**
- If the SDK alias for a method is missing, the generated wrapper **raises** a
  clear `NotImplementedError` telling which alias was expected.
- All responses are wrapped in a standardized `SlackResponse` object for consistent handling.

You can run this at program start to generate+import the class.

Examples
--------
```python
from slack_codegen import generate_slack_client, import_generated
from slack_sdk import WebClient as SlackClient

path = generate_slack_client(out_path="./slack_client.py")
SlackDataSource = import_generated(path, "SlackDataSource")

slack = SlackDataSource(SlackClient(token="xoxb-..."))
response = slack.chat_post_message(channel="#general", text="Hello")
if response.success:
    print(f"Message sent: {response.data}")
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
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

# Set up logger
logger = logging.getLogger(__name__)

# ---- Defaults --------------------------------------------------------------
DEFAULT_SPEC_URL = os.environ.get(
    "SLACK_OPENAPI_SPEC_URL",
    # The spec repo is archived; file is on the `master` branch
    "https://raw.githubusercontent.com/slackapi/slack-api-specs/master/web-api/slack_web_openapi_v2.json",
)
FALLBACK_SPEC_URLS = [
    # Kept for resilience if env overrides point elsewhere
    "https://raw.githubusercontent.com/slackapi/slack-api-specs/main/web-api/slack_web_openapi_v2.json",
]
DEFAULT_OUT = "slack_client.py"
DEFAULT_CLASS = "SlackDataSource"

# ---- Operation Model -------------------------------------------------------
@dataclass
class Operation:
    op_id: str            # e.g. "chat.postMessage"
    http_method: str      # e.g. "POST" | "GET" | ... (not used for transport here)
    path: str             # e.g. "/chat.postMessage"
    summary: str
    description: str
    params: List[Mapping[str, Any]]  # merged path+op params

    @property
    def client_alias(self) -> str:
        """SDK method name (Slack alias): dots → underscores, **preserve camelCase**.
        e.g. chat.postMessage → chat_postMessage, admin...convertToPrivate → admin_conversations_convertToPrivate
        """
        return self.op_id.replace(".", "_")

    @property
    def wrapper_name(self) -> str:
        """Wrapper method name: Fully snake_case (lowercase)."""
        return to_snake(self.client_alias)


# ---- Spec loading & parsing ------------------------------------------------
def _read_bytes_from_url(url: str) -> bytes:
    with urllib.request.urlopen(url) as resp:  # nosec - fetching public spec
        return resp.read()


def load_spec(*, spec_url: Optional[str] = None, spec_path: Optional[str] = None) -> Mapping[str, Any]:
    """Load Slack OpenAPI spec (JSON or YAML).

    Order:
      1) spec_path
      2) spec_url
      3) DEFAULT_SPEC_URL
      4) FALLBACK_SPEC_URLS
    """
    # 1) Local wins
    if spec_path:
        data = Path(spec_path).read_bytes()
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            try:
                import yaml  # type: ignore
            except Exception as e:  # pragma: no cover
                raise RuntimeError("Spec is not JSON and PyYAML is not installed") from e
            return yaml.safe_load(data)

    # 2..4) URLs
    tried: List[str] = []
    url_candidates = [u for u in [spec_url or DEFAULT_SPEC_URL] if u] + FALLBACK_SPEC_URLS
    for url in url_candidates:
        tried.append(url)
        try:
            data = _read_bytes_from_url(url)
            try:
                return json.loads(data.decode("utf-8"))
            except Exception:
                try:
                    import yaml  # type: ignore
                except Exception as e:  # pragma: no cover
                    raise RuntimeError("Spec is not JSON and PyYAML is not installed") from e
                return yaml.safe_load(data)
        except Exception:
            continue

    raise RuntimeError(
        "Failed to load Slack OpenAPI spec. Tried: " + ", ".join(tried) +
        "\nHint: pass --spec-path to a local copy, or set SLACK_OPENAPI_SPEC_URL."
    )


def extract_operations(spec: Mapping[str, Any]) -> List[Operation]:
    paths = spec.get("paths")
    if not isinstance(paths, Mapping):
        raise ValueError("Invalid OpenAPI spec: missing 'paths'")

    ops: List[Operation] = []
    for path, item in paths.items():
        if not isinstance(item, Mapping):
            continue
        for meth, op in item.items():
            if meth.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            if not isinstance(op, Mapping):
                continue
            op_id = op.get("operationId")
            if not op_id:
                tag = (op.get("tags") or ["api"])[0]
                name = path.strip("/").replace("/", ".")
                op_id = name if tag in name else f"{tag}.{name}"
            merged: List[Mapping[str, Any]] = []
            for p in item.get("parameters", []) + op.get("parameters", []):
                if isinstance(p, Mapping):
                    merged.append(p)
            ops.append(
                Operation(
                    op_id=str(op_id),
                    http_method=meth.upper(),
                    path=str(path),
                    summary=str(op.get("summary") or ""),
                    description=str(op.get("description") or ""),
                    params=merged,
                )
            )

    uniq: Dict[str, Operation] = {o.op_id: o for o in ops}
    return sorted(uniq.values(), key=lambda o: o.op_id)


# ---- Code generation helpers ----------------------------------------------
_IDENT_RE = re.compile(r"[^0-9a-zA-Z_]")


def sanitize_py_name(name: str) -> str:
    n = _IDENT_RE.sub("_", name)
    if n and n[0].isdigit():
        n = f"_{n}"
    if keyword.iskeyword(n):
        n += "_"
    if n.startswith("__"):
        n = f"_{n}"
    return n


def to_snake(name: str) -> str:
    """Convert a possibly camelCase/mixed name to snake_case (lower).
    Avoids regex backrefs so this source is safe to template into files.
    """
    n = name.replace(".", "_")
    out: List[str] = []
    for i, ch in enumerate(n):
        if ch.isupper():
            # Insert underscore on camel hump boundaries
            if i > 0 and (n[i-1].islower() or (i+1 < len(n) and n[i+1].islower())):
                out.append("_")
            out.append(ch.lower())
        else:
            out.append("_" if ch in "- " else ch)
    s = "".join(out)
    s = re.sub(r"__+", "_", s)
    return s


def param_sig_fragment(p: Mapping[str, Any]) -> Tuple[str, str, str]:
    api_name = str(p.get("name") or "param").strip()
    py_name = sanitize_py_name(api_name)
    default = "None" if not p.get("required") else "..."  # Ellipsis => required
    return py_name, api_name, default


def build_method_code(op: Operation) -> str:
    required_parts: List[str] = []
    optional_parts: List[str] = []
    arg_map_entries: List[str] = []
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
        arg_map_entries.append(f"            {py_name}={py_name},") if default == "..." else arg_map_entries.append(f"            {py_name}={py_name},")
        flag = "required" if default == "..." else "optional"
        desc = (p.get("description") or "").replace("\n", " ")
        docs_param_lines.append(f"            {py_name} ({flag}): {desc}")

    # Format parameters properly for multi-line signature  
    if required_parts or optional_parts:
        star = ["*"] if (required_parts or optional_parts) else []
        all_params = star + required_parts + optional_parts + ["**kwargs"]
        if len(all_params) <= 3:  # Short signature on one line
            sig = ", ".join(all_params)
        else:  # Multi-line signature
            params_formatted = ",\n        ".join(all_params)
            sig = f"\n        {params_formatted}\n    "
    else:
        sig = "**kwargs"

    summary = op.summary or op.op_id
    params_doc = "\n".join(docs_param_lines) if docs_param_lines else "            (no parameters)"
    
    # Build parameter mapping for kwargs
    param_mapping = []
    for p in op.params:
        api_name = str(p.get("name") or "param")
        py_name, api_name, default = param_sig_fragment(p)
        param_mapping.append(f"        if {py_name} is not None:\n            kwargs_api['{api_name}'] = {py_name}")

    param_mapping_code = "\n".join(param_mapping) if param_mapping else "        # No parameters for this method"

    method_code = f'''    async def {op.wrapper_name}(self, {sig}) -> SlackResponse:
        """{summary}

        Slack method: `{op.op_id}`  (HTTP {op.http_method} {op.path})

        Args:
{params_doc}

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.{op.client_alias}`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {{}}
{param_mapping_code}
        if kwargs:
            kwargs_api.update(kwargs)
        
        if not hasattr(self.client, '{op.client_alias}') or not callable(getattr(self.client, '{op.client_alias}')):
            return SlackResponse(
                success=False,
                error=f"Slack client is missing required method alias: {op.client_alias}"
            )
        
        try:
            response = getattr(self.client, '{op.client_alias}')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)
'''

    return method_code


def build_class_code(class_name: str, ops: Sequence[Operation]) -> str:
    methods = [build_method_code(o) for o in ops]

    header = f'''import logging
from typing import Any, Dict
from app.sources.client.slack.slack import SlackClient, SlackResponse

# Set up logger
logger = logging.getLogger(__name__)

class {class_name}:
    """Auto-generated Slack Web API client wrapper.
    - Uses the **official** SDK client passed as `SlackClient`
    - **Snake_case** method names; internally call SDK aliases (camelCase preserved)
    - **No fallback** to `api_call`: if SDK alias missing, returns SlackResponse with error
    - All responses wrapped in standardized SlackResponse format
    """
    def __init__(self, client: SlackClient) -> None:
        self.client = client.get_web_client()
'''

    runtime_helpers = """
    async def _handle_slack_response(self, response: Any) -> SlackResponse:  # noqa: ANN401
        \"\"\"Handle Slack API response and convert to standardized format\"\"\"
        try:
            if not response:
                return SlackResponse(success=False, error="Empty response from Slack API")
            
            # Extract data from SlackResponse object
            if hasattr(response, 'data'):
                data = response.data
            elif hasattr(response, 'get'):
                # Handle dict-like responses
                data = dict(response)
            else:
                data = {"raw_response": str(response)}

            # Check if response indicates success
            success = True
            error_msg = None
            
            # Most Slack API responses have an 'ok' field
            if isinstance(data, dict):
                if 'ok' in data:
                    success = data.get('ok', False)
                    if not success:
                        error_msg = data.get('error', 'Unknown Slack API error')
                elif 'error' in data:
                    success = False
                    error_msg = data.get('error')
            success = bool(data.get('ok', False))
            return SlackResponse(
                success=success, 
                data=data,
                error=error_msg
            )
        except Exception as e:
            logger.error(f"Error handling Slack response: {e}")
            return SlackResponse(success=False, error=str(e))

    async def _handle_slack_error(self, error: Exception) -> SlackResponse:
        \"\"\"Handle Slack API errors and convert to standardized format\"\"\"
        error_msg = str(error)
        logger.error(f"Slack API error: {error_msg}")
        return SlackResponse(success=False, error=error_msg)

"""

    return header + runtime_helpers + "\n" + "\n".join(methods) + f"\n\n__all__ = ['{class_name}', 'SlackResponse']\n"


# ---- Public entrypoints ----------------------------------------------------
def generate_slack_client(
    *,
    out_path: str = DEFAULT_OUT,
    class_name: str = DEFAULT_CLASS,
    spec_url: Optional[str] = None,
    spec_path: Optional[str] = None,
) -> str:
    """Generate the Slack client wrapper Python file. Returns its path."""
    spec = load_spec(spec_url=spec_url, spec_path=spec_path)
    ops = extract_operations(spec)
    code = build_class_code(class_name, ops)
    
    # Create slack directory in the same folder as this script
    script_dir = Path(__file__).parent if __file__ else Path('.')
    slack_dir = script_dir / 'slack'
    slack_dir.mkdir(exist_ok=True)
    
    # Set the full file path
    full_path = slack_dir / out_path
    full_path.write_text(code, encoding="utf-8")
    return str(full_path)


def import_generated(path: str, symbol: str = DEFAULT_CLASS):
    """Import the generated module (by filesystem path) and return a symbol."""
    module_name = Path(path).stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot import module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return getattr(module, symbol)


# ---- CLI & Self-tests ------------------------------------------------------
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate Slack Web API client (snake_case, with response handling)")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Output .py file path (default: slack_client.py)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--spec-url", default=None, help="Spec URL (overrides env/DEFAULT)")
    g.add_argument("--spec-path", help="Local spec file path (JSON/YAML)")
    ap.add_argument("--class-name", default=DEFAULT_CLASS, help="Generated class name (default: SlackDataSource)")
    ap.add_argument("--self-test", action="store_true", help="Run minimal self-tests and exit")
    return ap.parse_args(argv)


def _self_tests() -> None:
    """Offline tests: validate naming, invocation, and error behavior."""
    mini_spec = {
        "swagger": "2.0",
        "paths": {
            "/chat.postMessage": {
                "post": {
                    "operationId": "chat.postMessage",
                    "parameters": [
                        {"name": "channel", "in": "formData", "required": True, "type": "string"},
                        {"name": "text", "in": "formData", "required": False, "type": "string"},
                    ],
                }
            },
            "/admin.conversations.convertToPrivate": {
                "post": {
                    "operationId": "admin.conversations.convertToPrivate",
                    "parameters": [
                        {"name": "channel_id", "in": "formData", "required": True, "type": "string"},
                    ],
                }
            },
        },
    }

    ops = extract_operations(mini_spec)
    code = build_class_code("SlackDataSourceTest", ops)

    ns: Dict[str, Any] = {}
    exec(code, ns)
    TestCls = ns["SlackDataSourceTest"]
    SlackResponseCls = ns["SlackResponse"]

    # Client with only SDK aliases (camelCase preserved)
    class AliasClient:
        def chat_postMessage(self, **kwargs):
            return {"ok": True, "channel": kwargs.get("channel"), "text": kwargs.get("text")}
        def admin_conversations_convertToPrivate(self, **kwargs):
            return {"ok": True, "channel_id": kwargs.get("channel_id")}

    ds = TestCls(AliasClient())

    # 1) Wrapper names are fully snake_case
    assert hasattr(ds, "chat_post_message")
    assert hasattr(ds, "admin_conversations_convert_to_private")

    # 2) Calls route to SDK aliases and return SlackResponse
    r1 = ds.chat_post_message(channel="#g", text="hi")
    assert isinstance(r1, SlackResponseCls)
    assert r1.success
    assert r1.data["ok"] is True
    assert r1.data["channel"] == "#g"

    r2 = ds.admin_conversations_convert_to_private(channel_id="C1")
    assert isinstance(r2, SlackResponseCls)
    assert r2.success
    assert r2.data["channel_id"] == "C1"

    # 3) call(method, **params) resolves to wrapper and works
    r3 = ds.call("chat.postMessage", channel="#g2")
    assert isinstance(r3, SlackResponseCls)
    assert r3.success

    # 4) Missing alias should return SlackResponse with error (not raise exception)
    class MissingAliasClient:
        pass
    ds_missing = TestCls(MissingAliasClient())
    r4 = ds_missing.chat_post_message(channel="#x")
    assert isinstance(r4, SlackResponseCls)
    assert not r4.success
    assert "missing required method alias" in r4.error

    # 5) Test error response handling
    class ErrorClient:
        def chat_postMessage(self, **kwargs):
            return {"ok": False, "error": "channel_not_found"}
    
    ds_error = TestCls(ErrorClient())
    r5 = ds_error.chat_post_message(channel="#nonexistent")
    assert isinstance(r5, SlackResponseCls)
    assert not r5.success
    assert r5.error == "channel_not_found"

    print("✅ Self-tests passed")


def main(argv: Optional[Sequence[str]] = None) -> None:
    ns = _parse_args(argv)
    if ns.self_test:
        _self_tests()
        return
    out_path = generate_slack_client(
        out_path=ns.out, class_name=ns.class_name, spec_url=ns.spec_url, spec_path=ns.spec_path
    )
    print(f"✅ Generated {ns.class_name} -> {out_path}")
    print(f"📁 Files saved in: {Path(out_path).parent}")
    
    # Get generated method count
    spec = load_spec(spec_url=ns.spec_url, spec_path=ns.spec_path)
    ops = extract_operations(spec)
    print(f"📊 Generated {len(ops)} methods from Slack OpenAPI spec")


if __name__ == "__main__":
    main()