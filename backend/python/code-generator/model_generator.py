# ruff: noqa

# NOTE — Development-only generator
# This script is intended for development use only. It exists solely to
# download OpenAPI/Swagger specs and generate Pydantic model files + API client methods
# for connectors (e.g., Jira, Confluence). It is not part of the application
# runtime and should not be executed in production environments.
#
# Use it to refresh or (re)generate connector models during development or
# in CI jobs that update generated code. Treat the generated model files
# as the artifacts to be committed; the generator itself should not be
# imported or relied on by production code.

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
import json

CONNECTORS = {
    "jira": "https://developer.atlassian.com/cloud/jira/platform/swagger-v3.v3.json",
    "confluence": "https://developer.atlassian.com/cloud/confluence/swagger.v3.json",
}


def download_spec(connector: str, url: str, spec_file: Path):
    try:
        import requests
    except ImportError:
        print("📦 Installing 'requests'...")
        subprocess.run([sys.executable, "-m", "pip", "install", "requests"], check=True)
        import requests

    print(f"📥 Downloading {connector.capitalize()} OpenAPI spec from {url}")
    resp = requests.get(url)
    resp.raise_for_status()
    spec_file.write_text(resp.text, encoding="utf-8")
    print(f"✅ {connector.capitalize()} OpenAPI spec saved to {spec_file}")


def generate_models(connector: str, spec_file: Path, model_file: Path):
    subprocess.run([sys.executable, "-m", "pip", "install", "datamodel-code-generator"], check=True)
    print("✅ Installed datamodel-code-generator")

    cmd = [
        "datamodel-codegen",
        "--input", str(spec_file),
        "--input-file-type", "openapi",
        "--output", str(model_file),
        "--target-python-version", "3.10",
        "--use-standard-collections",
        "--output-model-type", "pydantic_v2.BaseModel"
    ]
    print(f"🚀 Generating Pydantic v2 models for {connector.capitalize()}...")
    subprocess.run(cmd, check=True)
    print(f"✅ {connector.capitalize()} models generated at {model_file}")


def fix_constr_calls(file_path: str):
    with open(file_path, "r") as f:
        content = f.read()

    imports_to_add = []
    if "Annotated" not in content:
        imports_to_add.append("from typing import Annotated")
    if "StringConstraints" not in content or "Field" not in content:
        imports_to_add.append("from pydantic import StringConstraints, Field")

    if imports_to_add:
        match = re.search(r"(^import[^\n]*\n|^from[^\n]*\n)+", content, flags=re.MULTILINE)
        if match:
            insert_pos = match.end()
            content = content[:insert_pos] + "\n".join(imports_to_add) + "\n\n" + content[insert_pos:]
        else:
            content = "\n".join(imports_to_add) + "\n\n" + content

    patterns = [
        (r'constr\(min_length=(\d+),\s*max_length=(\d+)\)',
         r'Annotated[str, StringConstraints(min_length=\1, max_length=\2)]'),
        (r'constr\(max_length=(\d+)\)',
         r'Annotated[str, StringConstraints(max_length=\1)]'),
        (r'constr\(min_length=(\d+)\)',
         r'Annotated[str, StringConstraints(min_length=\1)]'),
        (r'constr\(regex=["\']([^"\']+)["\']\)',
         r'Annotated[str, StringConstraints(pattern=r"\1")]'),
        (r'StrictStr', r'Annotated[str, StringConstraints(strict=True)]'),
        (r'StrictInt', r'Annotated[int, Field(strict=True)]'),
        (r'StrictFloat', r'Annotated[float, Field(strict=True)]'),
        (r'StrictBool', r'Annotated[bool, Field(strict=True)]'),
    ]

    for pattern, replacement in patterns:
        content = re.sub(pattern, replacement, content)

    with open(file_path, "w") as f:
        f.write(content)

    print(f"✅ Fixed constr(), Strict* types, and safely added imports in {file_path}")


def generate_client_methods(connector: str, spec_file: Path, client_file: Path):
    """Generate lightweight API client methods from OpenAPI paths."""
    print(f"⚙️ Generating API client methods for {connector.capitalize()}...")

    spec = json.loads(spec_file.read_text(encoding="utf-8"))
    paths = spec.get("paths", {})

    lines = [
        "from app.sources.client.http.http_response import HTTPResponse\n\n",
        "from app.sources.client.http.http_request import HTTPRequest\n\n",
        f"from app.sources.client.{connector}.{connector} import {connector[0].capitalize()}{connector[1:]}Client\n\n",
        f"class {connector.capitalize()}DataSource:\n",
        f"    def __init__(self, base_url: str, client: {connector.capitalize()}Client):\n",
        "        self.base_url = base_url.rstrip('/')\n",
        "        self.client = client\n\n",
        f"    def get_client(self) -> {connector.capitalize()}Client:\n",
        "        return self.client\n\n",
    ]

    for path, methods in paths.items():
        for http_method, details in methods.items():
            func_name = details.get("operationId") or f"{http_method}_{path.strip('/').replace('/', '_')}"
            func_name = re.sub(r'[^0-9a-zA-Z_]', '_', func_name)

            lines.append(f"    async def {func_name}(self, **kwargs) -> HTTPResponse:\n")
            lines.append(f"        \"\"\"Auto-generated from OpenAPI: {details.get('summary', '').strip()}\"\"\"\n")
            lines.append(f"        return await self.client.get_client().execute(HTTPRequest(method='{http_method}', url='{path}', body=kwargs))\n\n")

    client_file.write_text("".join(lines), encoding="utf-8")
    print(f"✅ Client methods generated at {client_file}")


def inspect_models(connector: str, model_file: Path):
    if not model_file.exists():
        print("⚠️ Models file not found.")
        return

    spec = importlib.util.spec_from_file_location(f"{connector}_models", model_file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    print(f"\n🧪 Sample models found in {connector.capitalize()} definitions:")
    for name in dir(module):
        if "issue" in name.lower() or "project" in name.lower():
            print(f" • {name}")


def process_connector(connector: str):
    base_dir = Path(__file__).parent
    output_dir = base_dir / connector
    output_dir.mkdir(exist_ok=True)

    spec_file = output_dir / f"{connector}_openapi.json"
    model_file = output_dir / f"{connector}_models.py"
    client_file = output_dir / f"{connector}_client.py"

    download_spec(connector, CONNECTORS[connector], spec_file)
    generate_models(connector, spec_file, model_file)
    fix_constr_calls(model_file)
    generate_client_methods(connector, spec_file, client_file)
    inspect_models(connector, model_file)


def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <connector|all>")
        print(f"Available connectors: {list(CONNECTORS.keys())} or 'all'")
        return

    choice = sys.argv[1].lower()
    if choice == "all":
        for connector in CONNECTORS.keys():
            print(f"\n🔹 Processing {connector.capitalize()}...")
            process_connector(connector)
    elif choice in CONNECTORS:
        process_connector(choice)
    else:
        print("❌ Invalid connector. Use one of:", list(CONNECTORS.keys()), "or 'all'")

    print("\n🎉 Done!")


if __name__ == "__main__":
    main()
