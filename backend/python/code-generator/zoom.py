#!/usr/bin/env python3
"""
FINAL ZOOM UNIFIED DATASOURCE GENERATOR (Rishabh-Approved)
----------------------------------------------------------

✔ No CLI arguments required
✔ Run generator using:  python zoom.py
✔ Auto-detects:
      - zoom_specs/ folder
      - output/zoom/ folder
✔ Never overwrites external runtime code
✔ Writes ONLY into local staging folder:
      backend/python/code-generator/output/zoom/

✔ Generates:
      - zoom.py
      - example.py
      - example_build_from_services.py
"""

import json
import re
from pathlib import Path
from typing import List

# ------------------------------------------------------------
# AUTO-DETECTED DIRECTORIES
# ------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
SPEC_DIR = SCRIPT_DIR / "zoom_specs"
OUTPUT_DIR = SCRIPT_DIR / "output" / "zoom"


# ------------------------------------------------------------
# UTILITIES
# ------------------------------------------------------------

PYTHON_KEYWORDS = {
    "from","in","class","global","nonlocal","for","while","if",
    "else","elif","try","except","finally","def","return","import",
    "as","with","raise","yield","lambda","pass","break","continue",
    "assert","del","not","or","and","is","async","await","type",
    "True","False","None"
}

def sanitize_param(name: str) -> str:
    """Convert Zoom param → safe Python identifier"""
    if not name:
        return "param"

    s = re.sub(r"[^0-9a-zA-Z_]", "_", name)

    if s and s[0].isdigit():
        s = "_" + s

    s = re.sub(r"_+", "_", s).strip("_")

    if s in PYTHON_KEYWORDS:
        s += "_"

    return s or "param"


def snake(name: str) -> str:
    """operationId → snake_case"""
    if not name:
        return "method"
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = s.replace("-", "_")
    s = re.sub(r"[^0-9a-zA-Z_]", "_", s)
    s = re.sub(r"_+", "_", s).lower().strip("_")
    if s and s[0].isdigit():
        s = "_" + s
    return s or "method"


def load_json(p: Path):
    with p.open("r", encoding="utf-8") as f:
        return json.load(f)


# ------------------------------------------------------------
# TEMPLATES
# ------------------------------------------------------------

HEADER = '''"""
AUTO-GENERATED ZOOM DATASOURCE — DO NOT MODIFY MANUALLY
"""
from typing import Any, Dict, Optional
from backend.python.app.sources.client.iclient import IClient
'''

CLASS_HEADER = '''
class ZoomDataSource:
    def __init__(self, client: IClient, base_url: str = "https://api.zoom.us/v2"):
        self._rest = client
        self._base_url = base_url.rstrip("/")
'''

METHOD_TEMPLATE = '''
    async def {method}(self{args}) -> Dict[str, Any]:
        """
        original_operation_id: {op}
        method: {verb}
        path: {raw_path}
        summary: {summary}
        """
        endpoint = f"{{self._base_url}}{sanitized_path}"
        params = {params_dict}
        body = None
{body_note}
        return await self._rest.request(
            "{verb}", endpoint, params=params, body=body, timeout=timeout
        )
'''


# ------------------------------------------------------------
# EXAMPLE.PY (OAuth Manual Flow)
# ------------------------------------------------------------

EXAMPLE_TEMPLATE = '''"""
ZoomDataSource Example (OAuth Authorization Code Flow)

HOW TO RUN THIS EXAMPLE:

1) From the repository root, run:
       python backend/python/code-generator/zoom.py

   This generates files into:
       backend/python/code-generator/output/zoom/

2) Copy the generated files into the external runtime folder:
       cp backend/python/code-generator/output/zoom/* \
          backend/python/app/sources/external/zoom/

3) Move into the external Zoom folder:
       cd backend/python/app/sources/external/zoom

4) Run the example:
       python example.py

   - A browser window will open
   - Login and authorize the Zoom OAuth app
   - Zoom will redirect to http://localhost:8080/callback
     (the page may show an error — this is OK)
   - Copy the `code=` value from the browser URL
   - Paste it into the terminal when prompted

This example demonstrates:
- OAuth Authorization Code flow
- Token exchange
- Calling real Zoom APIs (users, groups, chat, account)
- Graceful handling of feature-gated APIs
"""

import os
import sys
import asyncio
import webbrowser
from urllib.parse import urlencode

# -------------------------------------------------
# Ensure repo root + backend/python are importable
# -------------------------------------------------

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../../.."))
APP = os.path.join(ROOT, "backend", "python")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if APP not in sys.path:
    sys.path.insert(0, APP)

from backend.python.app.sources.client.zoom.zoom import (
    ZoomClient,
    ZoomOAuthConfig,
)
from backend.python.app.sources.external.zoom.zoom import ZoomDataSource

AUTH_URL = "https://zoom.us/oauth/authorize"


async def main() -> None:
    CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
    CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
    REDIRECT_URI = os.getenv("ZOOM_REDIRECT_URI", "http://localhost:8080/callback")

    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError("Set ZOOM_CLIENT_ID and ZOOM_CLIENT_SECRET")

    wrapper = ZoomClient.build_with_config(
        ZoomOAuthConfig(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
        )
    )

    rest = wrapper.get_client()
    ds = ZoomDataSource(rest)

    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
    }

    url = f"{AUTH_URL}?{urlencode(params)}"
    print("Open this URL & authorize:\\n", url)

    try:
        webbrowser.open(url)
    except Exception:
        pass

    code = input("\\nPaste the ?code= value here: ").strip()
    if not code:
        raise RuntimeError("No authorization code provided")

    await rest.exchange_code_for_token(code)

    print("\\nCalling users() ...")
    try:
        r = await ds.users()
        print(r.json())
    except Exception as e:
        print("users() failed:", e)

    print("\\nCalling groups() ...")
    try:
        r = await ds.groups()
        print(r.json())
    except Exception as e:
        print("groups() failed:", e)

    print("\\nCalling get_chat_sessions() ...")
    try:
        r = await ds.get_chat_sessions()
        print(r.json())
    except Exception as e:
        print("get_chat_sessions() failed:", e)

    print("\\nCalling get_a_billing_account() ...")
    try:
        r = await ds.get_a_billing_account()
        print(r.json())
    except Exception as e:
        print("get_a_billing_account() failed:", e)


if __name__ == "__main__":
    asyncio.run(main())
'''



# ------------------------------------------------------------
# BFS TEMPLATE (Slack/Notion Style)
# ------------------------------------------------------------

BFS_TEMPLATE = '''"""
Build-from-services example for Zoom
"""

import asyncio
import logging

from backend.python.app.sources.client.zoom.zoom import ZoomClient
from backend.python.app.sources.external.zoom.zoom import ZoomDataSource
from backend.python.app.config.configuration_service import ConfigurationService
from backend.python.app.config.providers.etcd.etcd3_encrypted_store import (
    Etcd3EncryptedKeyValueStore,
)


async def main() -> None:
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # Create ETCD store
    etcd_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # Create configuration service
    config_service = ConfigurationService(
        logger=logger,
        key_value_store=etcd_store,
    )

    # Build Zoom client using configuration service
    try:
        zoom_client = await ZoomClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print("✅ Zoom client created successfully")
    except Exception as e:
        logger.error(f"Failed to create Zoom client: {e}")
        print(f"❌ Error creating Zoom client: {e}")
        return

    # Create data source
    zoom_data_source = ZoomDataSource(zoom_client)

    # Test a simple API call (sanity check)
    try:
        response = await zoom_data_source.users()
        print(f"✅ Users response: {response}")
    except Exception as e:
        print(f"❌ Error calling users API: {e}")


if __name__ == "__main__":
    asyncio.run(main())
'''


# ------------------------------------------------------------
# GENERATOR LOGIC
# ------------------------------------------------------------

def generate(overwrite: bool = True):
    """
    Generates Zoom datasource + examples into OUTPUT_DIR.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    main_file = OUTPUT_DIR / "zoom.py"

    if main_file.exists() and not overwrite:
        print("zoom.py exists. Use overwrite=True to regenerate.")
        return

    # Load specs
    specs = []
    for f in sorted(SPEC_DIR.glob("*.json")):
        try:
            specs.append(load_json(f))
        except Exception:
            print(f"Skipping bad JSON: {f}")

    endpoints = []

    for spec in specs:
        paths = spec.get("paths", {}) or {}
        for raw_path, verbs in paths.items():
            if not isinstance(verbs, dict):
                continue
            for verb, op in verbs.items():
                if not isinstance(op, dict):
                    continue
                endpoints.append({
                    "operationId": op.get("operationId", ""),
                    "summary": op.get("summary", "") or "",
                    "path": raw_path,
                    "verb": verb.upper(),
                    "params": op.get("parameters", []) or [],
                    "body": op.get("requestBody")
                })

    used = set()
    lines: List[str] = [HEADER, CLASS_HEADER]

    # Generate all Python methods
    for ep in endpoints:

        op = ep["operationId"]
        summary = (ep["summary"] or "").replace('"', "'")
        raw_path = ep["path"]
        verb = ep["verb"]

        # Create method name
        base = snake(op if op else f"{verb}_{raw_path}")
        name = base
        i = 1
        while name in used:
            name = f"{base}_{i}"
            i += 1
        used.add(name)

        # Params
        path_ph = re.findall(r"{([^}]+)}", raw_path)
        params = []
        param_map = []
        seen = set()

        for p in ep["params"]:
            if not isinstance(p, dict):
                continue
            pname = p.get("name")
            if not pname:
                continue

            safe = sanitize_param(pname)
            if safe in seen:
                idx = 1
                while f"{safe}_{idx}" in seen:
                    idx += 1
                safe = f"{safe}_{idx}"

            seen.add(safe)
            params.append(f"{safe}: Optional[Any] = None")
            param_map.append(f"'{pname}': {safe}")

        # Add missing path variables
        for ph in path_ph:
            safe = sanitize_param(ph)
            if safe not in seen:
                seen.add(safe)
                params.append(f"{safe}: Optional[Any] = None")
                param_map.append(f"'{ph}': {safe}")

        # Add timeout
        params.append("timeout: Optional[int] = None")

        # Replace path placeholders
        sanitized_path = re.sub(
            r"{([^}]+)}",
            lambda m: "{" + sanitize_param(m.group(1)) + "}",
            raw_path
        )

        body_note = (
            "        # This endpoint accepts a request body.\n        body = None"
            if ep["body"] else
            "        body = None"
        )

        method_src = METHOD_TEMPLATE.format(
            method=name,
            args=", " + ", ".join(params) if params else "",
            op=op or "<none>",
            verb=verb,
            raw_path=raw_path,
            sanitized_path=sanitized_path,
            summary=summary,
            params_dict="{ " + ", ".join(param_map) + " }",
            body_note=body_note,
        )

        lines.append(method_src)

    # Write output files
    main_file.write_text("\n".join(lines), encoding="utf-8")
    (OUTPUT_DIR / "example.py").write_text(EXAMPLE_TEMPLATE, encoding="utf-8")
    (OUTPUT_DIR / "example_build_from_services.py").write_text(BFS_TEMPLATE, encoding="utf-8")

    print(f"✅ Generated zoom.py with {len(endpoints)} endpoints")
    print(f"📄 Example files written to {OUTPUT_DIR}")
    print("⚠️ Manually copy files to backend/python/app/sources/external/zoom/")


# ------------------------------------------------------------
# ENTRYPOINT — JUST run:  python zoom.py
# ------------------------------------------------------------

if __name__ == "__main__":
    generate(overwrite=True)
