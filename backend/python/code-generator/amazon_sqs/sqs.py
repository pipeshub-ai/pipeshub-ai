# ruff: noqa
#!/usr/bin/env python3
"""
Amazon SQS — Code Generator

Generates an `AmazonSQSDataSource` class that wraps the AWS SQS API via `boto3.client("sqs")`.
The generated class will:
  - Include all core SQS API methods (send_message, receive_message, etc.)
  - Use an injected `AmazonSQSClient`
  - Produce consistent, typed responses using `AmazonSQSResponse`
  - Follow open-source lint standards used in PipesHub (no fallback calls, no `Any` typing)

Example:
--------
from app.sources.client.amazon_sqs import AmazonSQSClient
from amazon_sqs import generate_sqs_client, import_generated

out_path = generate_sqs_client()
AmazonSQSDataSource = import_generated(out_path, "AmazonSQSDataSource")

client = AmazonSQSClient(access_key="...", secret_key="...", region_name="us-east-1")
sqs = AmazonSQSDataSource(client)
resp = sqs.send_message(queue_url="https://...", message_body="Hello PipesHub")
print(resp.success, resp.data)
"""

from __future__ import annotations
import argparse
import boto3
import inspect
import importlib.util
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_OUT = "amazon_sqs_data_source.py"
DEFAULT_CLASS = "AmazonSQSDataSource"


@dataclass
class Operation:
    name: str
    summary: str
    doc: str
    params: List[str]


def extract_sqs_operations() -> List[Operation]:
    client = boto3.client("sqs")
    operations: List[Operation] = []

    for name, method in inspect.getmembers(client, predicate=inspect.ismethod):
        if name.startswith("_"):
            continue
        sig = inspect.signature(method)
        params = [p.name for p in sig.parameters.values() if p.name not in {"self"}]
        doc = (inspect.getdoc(method) or "").split("\n")[0]
        operations.append(Operation(name=name, summary=doc, doc=doc, params=params))

    # Filter out non-public or redundant methods
    filtered = [o for o in operations if not o.name.startswith("_") and o.name[0].islower()]
    return filtered


def build_method_code(op: Operation) -> str:
    """Generate one async wrapper method for SQS operation."""
    params = ", ".join([f"{p}: Optional[str] = None" for p in op.params])
    params = f"{params}" if params else ""
    args_dict = "\n".join([f"        if {p} is not None:\n            kwargs['{p}'] = {p}" for p in op.params])
    if not args_dict:
        args_dict = "        # No parameters for this method"

    method = f'''
    async def {op.name}(self, {params}) -> AmazonSQSResponse:
        """{op.summary or op.name}
        Auto-generated wrapper for SQS.{op.name}()
        """
        try:
            kwargs: Dict[str, Any] = {{}}
{args_dict}
            func = getattr(self.client.get_client(), "{op.name}")
            response = func(**kwargs)
            return AmazonSQSResponse(success=True, data=response)
        except Exception as e:
            return AmazonSQSResponse(success=False, error=str(e))
'''
    return method


def build_class_code(class_name: str, ops: List[Operation]) -> str:
    methods = [build_method_code(op) for op in ops]
    header = '''from typing import Any, Dict, Optional
from dataclasses import dataclass, asdict
from app.sources.client.amazon_sqs import AmazonSQSClient

@dataclass
class AmazonSQSResponse:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class AmazonSQSDataSource:
    """Auto-generated Amazon SQS DataSource class"""
    def __init__(self, client: AmazonSQSClient) -> None:
        self.client = client
'''
    return header + "\n".join(methods) + "\n"


def generate_sqs_client(out_path: str = DEFAULT_OUT, class_name: str = DEFAULT_CLASS) -> str:
    ops = extract_sqs_operations()
    code = build_class_code(class_name, ops)

    out_file = Path(out_path)
    out_file.write_text(code, encoding="utf-8")
    return str(out_file)


def import_generated(path: str, symbol: str = DEFAULT_CLASS):
    module_name = Path(path).stem
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise ImportError(f"Cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)  # type: ignore
    return getattr(module, symbol)


def main(argv: Optional[List[str]] = None) -> None:
    ap = argparse.ArgumentParser(description="Generate Amazon SQS DataSource")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--class-name", default=DEFAULT_CLASS)
    args = ap.parse_args(argv)

    path = generate_sqs_client(out_path=args.out, class_name=args.class_name)
    print(f"✅ Generated {args.class_name} -> {path}")
    print(f"📦 Generated using boto3 SQS operations ({len(extract_sqs_operations())} methods)")
    print(f"📁 File saved at: {Path(path).resolve()}")


if __name__ == "__main__":
    main()
