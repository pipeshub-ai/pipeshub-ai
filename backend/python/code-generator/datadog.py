#!/usr/bin/env python3
# ruff: noqa
"""
DataDog API — Code Generator

Generates a `DataDogDataSource` class that wraps the DataDog SDK client.
Since DataDog provides an official Python SDK (datadog-api-client), we generate
wrapper methods for common API operations.

The generator creates methods for:
- Monitors API (list, get, create, update, delete)
- Dashboards API (list, get, create, update, delete)
- Logs API (list, aggregate)
- Metrics API (query, list active, search)

All methods follow snake_case naming and return DataDogResponse for consistency.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

# Set up logger
logger = logging.getLogger(__name__)

DEFAULT_OUT = "datadog.py"
DEFAULT_CLASS = "DataDogDataSource"


# ---- API Method Definitions ------------------------------------------------
# Define the methods we want to generate based on DataDog SDK capabilities

API_METHODS = {
    "monitors": [
        {
            "name": "list_monitors",
            "sdk_method": "list_monitors",
            "api": "monitors_api",
            "summary": "List all monitors",
            "params": [
                {"name": "group_states", "type": "str", "required": False, "desc": "Comma-separated list of states to filter by"},
                {"name": "name", "type": "str", "required": False, "desc": "String to filter monitors by name"},
                {"name": "tags", "type": "str", "required": False, "desc": "Comma-separated list of tags"},
                {"name": "monitor_tags", "type": "str", "required": False, "desc": "Comma-separated list of monitor tags"},
                {"name": "with_downtimes", "type": "bool", "required": False, "desc": "Include downtime info"},
            ],
        },
        {
            "name": "get_monitor",
            "sdk_method": "get_monitor",
            "api": "monitors_api",
            "summary": "Get a monitor by ID",
            "params": [
                {"name": "monitor_id", "type": "int", "required": True, "desc": "Monitor ID"},
                {"name": "group_states", "type": "str", "required": False, "desc": "Group states to include"},
            ],
        },
        {
            "name": "create_monitor",
            "sdk_method": "create_monitor",
            "api": "monitors_api",
            "summary": "Create a new monitor",
            "params": [
                {"name": "body", "type": "dict", "required": True, "desc": "Monitor definition"},
            ],
        },
        {
            "name": "update_monitor",
            "sdk_method": "update_monitor",
            "api": "monitors_api",
            "summary": "Update an existing monitor",
            "params": [
                {"name": "monitor_id", "type": "int", "required": True, "desc": "Monitor ID"},
                {"name": "body", "type": "dict", "required": True, "desc": "Monitor update definition"},
            ],
        },
        {
            "name": "delete_monitor",
            "sdk_method": "delete_monitor",
            "api": "monitors_api",
            "summary": "Delete a monitor",
            "params": [
                {"name": "monitor_id", "type": "int", "required": True, "desc": "Monitor ID to delete"},
            ],
        },
    ],
    "dashboards": [
        {
            "name": "list_dashboards",
            "sdk_method": "list_dashboards",
            "api": "dashboards_api",
            "summary": "List all dashboards",
            "params": [
                {"name": "filter_shared", "type": "bool", "required": False, "desc": "Filter shared dashboards"},
                {"name": "filter_deleted", "type": "bool", "required": False, "desc": "Filter deleted dashboards"},
            ],
        },
        {
            "name": "get_dashboard",
            "sdk_method": "get_dashboard",
            "api": "dashboards_api",
            "summary": "Get a dashboard by ID",
            "params": [
                {"name": "dashboard_id", "type": "str", "required": True, "desc": "Dashboard ID"},
            ],
        },
        {
            "name": "create_dashboard",
            "sdk_method": "create_dashboard",
            "api": "dashboards_api",
            "summary": "Create a new dashboard",
            "params": [
                {"name": "body", "type": "dict", "required": True, "desc": "Dashboard definition"},
            ],
        },
        {
            "name": "update_dashboard",
            "sdk_method": "update_dashboard",
            "api": "dashboards_api",
            "summary": "Update an existing dashboard",
            "params": [
                {"name": "dashboard_id", "type": "str", "required": True, "desc": "Dashboard ID"},
                {"name": "body", "type": "dict", "required": True, "desc": "Dashboard update definition"},
            ],
        },
        {
            "name": "delete_dashboard",
            "sdk_method": "delete_dashboard",
            "api": "dashboards_api",
            "summary": "Delete a dashboard",
            "params": [
                {"name": "dashboard_id", "type": "str", "required": True, "desc": "Dashboard ID to delete"},
            ],
        },
    ],
    "logs": [
        {
            "name": "list_logs",
            "sdk_method": "list_logs",
            "api": "logs_api",
            "summary": "List logs with search query",
            "params": [
                {"name": "body", "type": "dict", "required": True, "desc": "Log search request body"},
            ],
        },
        {
            "name": "aggregate_logs",
            "sdk_method": "aggregate_logs",
            "api": "logs_api",
            "summary": "Aggregate logs",
            "params": [
                {"name": "body", "type": "dict", "required": True, "desc": "Log aggregation request body"},
            ],
        },
    ],
    "metrics": [
        {
            "name": "query_metrics",
            "sdk_method": "query_metrics",
            "api": "metrics_api",
            "summary": "Query metrics timeseries",
            "params": [
                {"name": "query", "type": "str", "required": True, "desc": "Metric query string"},
                {"name": "from_ts", "type": "int", "required": True, "desc": "Start timestamp (epoch seconds)"},
                {"name": "to_ts", "type": "int", "required": True, "desc": "End timestamp (epoch seconds)"},
            ],
        },
        {
            "name": "list_active_metrics",
            "sdk_method": "list_active_metrics",
            "api": "metrics_api",
            "summary": "List active metrics",
            "params": [
                {"name": "from_ts", "type": "int", "required": True, "desc": "Start timestamp (epoch seconds)"},
                {"name": "host", "type": "str", "required": False, "desc": "Hostname to filter"},
                {"name": "tag_filter", "type": "str", "required": False, "desc": "Tag filter"},
            ],
        },
    ],
}


# ---- Code generation helpers -----------------------------------------------
def build_method_signature(method: Dict[str, Any]) -> str:
    """Build method signature with parameters."""
    required_params = [p for p in method["params"] if p["required"]]
    optional_params = [p for p in method["params"] if not p["required"]]
    
    sig_parts = ["self"]
    
    # Add required params
    for p in required_params:
        sig_parts.append(f"{p['name']}: {p['type']}")
    
    # Add optional params
    for p in optional_params:
        default = "None" if p["type"] != "bool" else "False"
        sig_parts.append(f"{p['name']}: Optional[{p['type']}] = {default}")
    
    # Add **kwargs
    sig_parts.append("**kwargs: Any")
    
    return ", ".join(sig_parts)


def build_params_doc(method: Dict[str, Any]) -> str:
    """Build parameter documentation."""
    if not method["params"]:
        return "            (no parameters)"
    
    lines = []
    for p in method["params"]:
        flag = "required" if p["required"] else "optional"
        lines.append(f"            {p['name']} ({flag}): {p['desc']}")
    
    return "\n".join(lines)



def build_sdk_call(method: Dict[str, Any]) -> str:
    """Build the SDK method call with parameter mapping."""
    sdk_method = method["sdk_method"]

    lines = ["kwargs_api: Dict[str, Any] = {}"]
    for p in method["params"]:
        name = p["name"]
        if p["required"]:
            lines.append(f"kwargs_api['{name}'] = {name}")
        else:
            lines.append(f"if {name} is not None:")
            lines.append(f"    kwargs_api['{name}'] = {name}")

    lines.extend([
        "if kwargs:",
        "    kwargs_api.update(kwargs)",
        f"response = self.client.{sdk_method}(**kwargs_api)",
        f"return self._handle_response(response, \"{method['name']}\")"
    ])

    # Indent method internals by 12 spaces (inside class + method + try)
    return textwrap.indent("\n".join(lines), " " * 12)


def build_method_code(method: Dict[str, Any]) -> str:
    """Generate properly indented code for a single method inside the class."""
    sig = build_method_signature(method)
    params_doc = build_params_doc(method)
    sdk_call = build_sdk_call(method)

    method_code = f"""
    def {method["name"]}({sig}) -> DataDogResponse:
        \"\"\"{method["summary"]}

        DataDog SDK method: `{method["sdk_method"]}`

        Args:
{params_doc}

        Returns:
            DataDogResponse: Standardized response wrapper with success/data/error
        \"\"\"
        self.logger.info(f"Calling DataDog API: {method['name']}")
        try:
{sdk_call}
        except Exception as e:
            self.logger.error(f"DataDog API error in {method['name']}: {{e}}", exc_info=True)
            return DataDogResponse(success=False, error=str(e))
"""
    # Ensure the method code is indented 4 spaces (inside the class)
    return textwrap.indent(textwrap.dedent(method_code).rstrip(), " " * 4) + "\n\n"


def build_class_code(class_name: str) -> str:
    """Generate the complete DataDogDataSource class."""
    
    header = f'''"""Auto-generated DataDog DataSource wrapper.

This module is generated from DataDog SDK API definitions.
All methods return DataDogResponse for consistent error handling.
"""

import logging
from typing import Any, Dict, Optional

from app.sources.client.datadog.datadog import DataDogClient, DataDogResponse

logger = logging.getLogger(__name__)


class {class_name}:
    """Auto-generated DataDog API client wrapper.
    
    - Wraps the official DataDog SDK client (DataDogClient)
    - Snake_case method names for Python conventions
    - All responses wrapped in standardized DataDogResponse format
    - Comprehensive logging for debugging
    
    Generated methods cover:
    - Monitors: list, get, create, update, delete
    - Dashboards: list, get, create, update, delete
    - Logs: list, aggregate
    - Metrics: query, list active
    """
    
    def __init__(self, client: DataDogClient) -> None:
        """Initialize DataDog DataSource.
        
        Args:
            client: Initialized DataDogClient instance
        """
        self.client = client
        self.logger = logging.getLogger(__name__)
        self.logger.info("DataDogDataSource initialized")
    
    def _handle_response(self, response: DataDogResponse, method_name: str) -> DataDogResponse:
        """Handle DataDog client response.
        
        Args:
            response: Response from DataDogClient
            method_name: Name of the method being called
            
        Returns:
            DataDogResponse: The response object
        """
        if response.success:
            self.logger.info(f"DataDog API call successful: {{method_name}}")
        else:
            self.logger.error(f"DataDog API call failed: {{method_name}} - {{response.error}}")
        return response

'''
    
    # Generate methods for each API category
    all_methods = []
    for category, methods in API_METHODS.items():
        all_methods.append(f"\n    # ==================== {category.title()} API ====================\n")
        for method in methods:
            all_methods.append(build_method_code(method))
    
    footer = f'\n\n__all__ = ["{class_name}", "DataDogResponse"]\n'
    
    return header + "".join(all_methods) + footer


# ---- Public entrypoints ----------------------------------------------------
def generate_datadog_client(
    *,
    out_path: str = DEFAULT_OUT,
    class_name: str = DEFAULT_CLASS,
) -> str:
    """Generate the DataDog client wrapper Python file. Returns its path."""
    code = build_class_code(class_name)
    
    # Create datadog directory in the same folder as this script
    script_dir = Path(__file__).parent if __file__ else Path('.')
    datadog_dir = Path(r'C:\Users\Dell\pipeshub-ai\backend\python\app\sources\external\datadog')
    datadog_dir.mkdir(exist_ok=True)
    
    # Set the full file path
    full_path = datadog_dir / out_path
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


# ---- CLI -------------------------------------------------------------------
def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate DataDog API client wrapper")
    ap.add_argument("--out", default=DEFAULT_OUT, help="Output .py file path (default: datadog_client.py)")
    ap.add_argument("--class-name", default=DEFAULT_CLASS, help="Generated class name (default: DataDogDataSource)")
    ap.add_argument("--self-test", action="store_true", help="Run minimal self-tests and exit")
    return ap.parse_args(argv)


def _self_tests() -> None:
    """Run self-tests to validate code generation."""
    code = build_class_code("DataDogDataSourceTest")
    
    # Verify code compiles
    try:
        compile(code, "<generated>", "exec")
        print("Generated code compiles successfully")
    except SyntaxError as e:
        print(f"Generated code has syntax errors: {e}")
        return
    
    # Count generated methods
    method_count = sum(len(methods) for methods in API_METHODS.values())
    print(f"Generated {method_count} methods across {len(API_METHODS)} API categories")
    
    # Verify each category has methods
    for category, methods in API_METHODS.items():
        print(f"   - {category.title()}: {len(methods)} methods")
    
    print("Self-tests passed")


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Main CLI entry point."""
    ns = _parse_args(argv)
    
    if ns.self_test:
        _self_tests()
        return
    
    out_path = generate_datadog_client(
        out_path=ns.out,
        class_name=ns.class_name,
    )
    
    method_count = sum(len(methods) for methods in API_METHODS.values())
    print(f"Generated {ns.class_name} -> {out_path}")
    print(f"Files saved in: {Path(out_path).parent}")
    print(f"Generated {method_count} methods from DataDog SDK")


if __name__ == "__main__":
    main()