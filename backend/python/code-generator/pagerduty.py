# ruff: noqa
"""
PagerDuty API Client Generator - SDK Introspection Method

Generates PagerDutyDataSource class by introspecting the official PagerDuty SDK (>=5.0.0).
Uses RestApiV2Client from the pagerduty package.
This bypasses OpenAPI spec parsing issues by directly analyzing the SDK.

Usage:
    python pagerduty.py
    python pagerduty.py --test

Requirements:
    pagerduty>=5.0.0 (uses RestApiV2Client, not the deprecated pdpyras)
"""

import argparse
import inspect
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Fix import conflict: remove current directory from path before importing pagerduty package
_current_dir = str(Path(__file__).parent)
if _current_dir in sys.path:
    sys.path.remove(_current_dir)

try:
    from pagerduty import RestApiV2Client, EventsApiV2Client  # Official PagerDuty SDK v5.0+
except ImportError:
    print("Error: pagerduty SDK not found")
    print("Installing PagerDuty SDK (>=5.0.0)...")
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "pagerduty>=5.0.0"], check=True)
    from pagerduty import RestApiV2Client, EventsApiV2Client
finally:
    # Restore path
    if _current_dir not in sys.path:
        sys.path.insert(0, _current_dir)

# Set up logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_OUT = "pagerduty_data_source.py"
DEFAULT_CLASS = "PagerDutyDataSource"


class PagerDutySDKMethodDiscoverer:
    """Discovers and analyzes PagerDuty SDK methods."""
    
    def __init__(self):
        self.generated_methods: List[Dict[str, Any]] = []
        self.sdk_methods: Dict[str, Any] = {}
    
    def discover_api_methods(self) -> List[Dict[str, Any]]:
        """Discover all PagerDuty SDK methods."""
        methods = []
        
        # Create a dummy session to introspect
        try:
            # We'll inspect the RestApiV2Client class itself
            api_session_methods = self._get_class_methods(RestApiV2Client)
            
            for method_name, method_obj in api_session_methods.items():
                if method_name.startswith('_'):
                    continue
                    
                # Skip certain utility methods
                skip_methods = {
                    'add_header', 'remove_header', 'prepare_headers',
                    'raise_if_http_error', 'subdomain_from_url',
                    'normalize_url', 'set_api_key', 'truncate',
                    'log', 'profiler_key'
                }
                
                if method_name in skip_methods:
                    continue
                
                method_info = self._extract_method_info(method_name, method_obj)
                if method_info:
                    methods.append(method_info)
        
        except Exception as e:
            logger.error(f"Error discovering API methods: {e}")
        
        # Add manually defined high-level methods
        methods.extend(self._get_manual_methods())
        
        return methods
    
    def _get_class_methods(self, cls: type) -> Dict[str, Any]:
        """Get all methods from a class."""
        methods = {}
        for name, obj in inspect.getmembers(cls):
            if inspect.ismethod(obj) or inspect.isfunction(obj):
                methods[name] = obj
        return methods
    
    def _extract_method_info(self, method_name: str, method_obj: Any) -> Optional[Dict[str, Any]]:
        """Extract method information."""
        try:
            sig = inspect.signature(method_obj)
            parameters = {}
            
            for param_name, param in sig.parameters.items():
                if param_name in ['self', 'cls']:
                    continue
                
                param_info = {
                    "name": param_name,
                    "type": "Any",
                    "required": param.default == inspect.Parameter.empty,
                    "default": None if param.default == inspect.Parameter.empty else param.default,
                }
                parameters[param_name] = param_info
            
            docstring = inspect.getdoc(method_obj) or f"PagerDuty SDK method: {method_name}"
            
            return {
                "method_name": method_name,
                "sdk_method": method_name,
                "parameters": parameters,
                "docstring": docstring,
                "category": self._categorize_method(method_name)
            }
        
        except Exception as e:
            logger.debug(f"Failed to extract info for {method_name}: {e}")
            return None
    
    def _categorize_method(self, method_name: str) -> str:
        """Categorize method based on name."""
        if 'incident' in method_name.lower():
            return "Incidents"
        elif 'service' in method_name.lower():
            return "Services"
        elif 'user' in method_name.lower():
            return "Users"
        elif 'schedule' in method_name.lower():
            return "Schedules"
        elif 'escalation' in method_name.lower():
            return "Escalation Policies"
        elif 'oncall' in method_name.lower() or 'on_call' in method_name.lower():
            return "On-Call"
        elif 'team' in method_name.lower():
            return "Teams"
        else:
            return "General"
    
    def _get_manual_methods(self) -> List[Dict[str, Any]]:
        """Define commonly used PagerDuty API methods manually."""
        return [
            {
                "method_name": "list_incidents",
                "sdk_method": "list_all",
                "parameters": {
                    "endpoint": {"name": "endpoint", "type": "str", "required": True, "default": "'incidents'"},
                    "params": {"name": "params", "type": "Optional[Dict[str, Any]]", "required": False, "default": None}
                },
                "docstring": "List all incidents with optional filtering",
                "category": "Incidents",
                "custom_impl": True
            },
            {
                "method_name": "get_incident",
                "sdk_method": "rget",
                "parameters": {
                    "incident_id": {"name": "incident_id", "type": "str", "required": True, "default": None}
                },
                "docstring": "Get a specific incident by ID",
                "category": "Incidents",
                "custom_impl": True
            },
            {
                "method_name": "create_incident",
                "sdk_method": "rpost",
                "parameters": {
                    "data": {"name": "data", "type": "Dict[str, Any]", "required": True, "default": None}
                },
                "docstring": "Create a new incident",
                "category": "Incidents",
                "custom_impl": True
            },
            {
                "method_name": "update_incident",
                "sdk_method": "rput",
                "parameters": {
                    "incident_id": {"name": "incident_id", "type": "str", "required": True, "default": None},
                    "data": {"name": "data", "type": "Dict[str, Any]", "required": True, "default": None}
                },
                "docstring": "Update an existing incident",
                "category": "Incidents",
                "custom_impl": True
            },
            {
                "method_name": "list_services",
                "sdk_method": "list_all",
                "parameters": {
                    "endpoint": {"name": "endpoint", "type": "str", "required": True, "default": "'services'"},
                    "params": {"name": "params", "type": "Optional[Dict[str, Any]]", "required": False, "default": None}
                },
                "docstring": "List all services",
                "category": "Services",
                "custom_impl": True
            },
            {
                "method_name": "get_service",
                "sdk_method": "rget",
                "parameters": {
                    "service_id": {"name": "service_id", "type": "str", "required": True, "default": None}
                },
                "docstring": "Get a specific service by ID",
                "category": "Services",
                "custom_impl": True
            },
            {
                "method_name": "list_users",
                "sdk_method": "list_all",
                "parameters": {
                    "endpoint": {"name": "endpoint", "type": "str", "required": True, "default": "'users'"},
                    "params": {"name": "params", "type": "Optional[Dict[str, Any]]", "required": False, "default": None}
                },
                "docstring": "List all users",
                "category": "Users",
                "custom_impl": True
            },
            {
                "method_name": "get_user",
                "sdk_method": "rget",
                "parameters": {
                    "user_id": {"name": "user_id", "type": "str", "required": True, "default": None}
                },
                "docstring": "Get a specific user by ID",
                "category": "Users",
                "custom_impl": True
            },
            {
                "method_name": "list_schedules",
                "sdk_method": "list_all",
                "parameters": {
                    "endpoint": {"name": "endpoint", "type": "str", "required": True, "default": "'schedules'"},
                    "params": {"name": "params", "type": "Optional[Dict[str, Any]]", "required": False, "default": None}
                },
                "docstring": "List all schedules",
                "category": "Schedules",
                "custom_impl": True
            },
            {
                "method_name": "get_schedule",
                "sdk_method": "rget",
                "parameters": {
                    "schedule_id": {"name": "schedule_id", "type": "str", "required": True, "default": None}
                },
                "docstring": "Get a specific schedule by ID",
                "category": "Schedules",
                "custom_impl": True
            },
            {
                "method_name": "list_escalation_policies",
                "sdk_method": "list_all",
                "parameters": {
                    "endpoint": {"name": "endpoint", "type": "str", "required": True, "default": "'escalation_policies'"},
                    "params": {"name": "params", "type": "Optional[Dict[str, Any]]", "required": False, "default": None}
                },
                "docstring": "List all escalation policies",
                "category": "Escalation Policies",
                "custom_impl": True
            },
            {
                "method_name": "list_oncalls",
                "sdk_method": "list_all",
                "parameters": {
                    "endpoint": {"name": "endpoint", "type": "str", "required": True, "default": "'oncalls'"},
                    "params": {"name": "params", "type": "Optional[Dict[str, Any]]", "required": False, "default": None}
                },
                "docstring": "List current on-call users",
                "category": "On-Call",
                "custom_impl": True
            },
            {
                "method_name": "list_teams",
                "sdk_method": "list_all",
                "parameters": {
                    "endpoint": {"name": "endpoint", "type": "str", "required": True, "default": "'teams'"},
                    "params": {"name": "params", "type": "Optional[Dict[str, Any]]", "required": False, "default": None}
                },
                "docstring": "List all teams",
                "category": "Teams",
                "custom_impl": True
            },
        ]


def generate_method_code(method_info: Dict[str, Any]) -> str:
    """Generate Python code for a single method."""
    method_name = method_info["method_name"]
    sdk_method = method_info.get("sdk_method", method_name)
    parameters = method_info.get("parameters", {})
    docstring = method_info.get("docstring", f"PagerDuty method: {method_name}")
    custom_impl = method_info.get("custom_impl", False)
    
    # Build parameter list - separate required and optional for proper ordering
    required_params = []
    optional_params = []
    param_docs = []
    
    for param_name, param_info in parameters.items():
        param_type = param_info.get("type", "Any")
        required = param_info.get("required", False)
        default = param_info.get("default")
        
        if required and default is None:
            required_params.append(f"{param_name}: {param_type}")
        elif default is not None and isinstance(default, str) and default.startswith("'"):
            optional_params.append(f"{param_name}: {param_type} = {default}")
        else:
            optional_params.append(f"{param_name}: {param_type} = None")
        
        param_docs.append(f"            {param_name}: {param_info.get('description', 'Parameter')}")
    
    # Combine: self, required params, then optional params
    param_list = ["self"] + required_params + optional_params
    params_str = ",\n        ".join(param_list)
    params_doc = "\n".join(param_docs) if param_docs else "            No parameters"
    
    # Generate method implementation based on type
    if custom_impl:
        if sdk_method == "list_all":
            impl = f"""        return self.client.list_all(endpoint, params=params or {{}})"""
        elif sdk_method == "rget":
            param_names = list(parameters.keys())
            if param_names:
                first_param = param_names[0]
                impl = f"""        endpoint = f"{method_name.replace('get_', '').replace('list_', '')}s/{{{first_param}}}"
        return self.client.rget(endpoint)"""
            else:
                impl = f"""        return self.client.rget("{method_name}")"""
        elif sdk_method == "rpost":
            impl = f"""        return self.client.rpost("incidents", json=data)"""
        elif sdk_method == "rput":
            param_names = list(parameters.keys())
            if len(param_names) >= 2:
                id_param = param_names[0]
                impl = f"""        endpoint = f"incidents/{{{id_param}}}"
        return self.client.rput(endpoint, json=data)"""
            else:
                impl = f"""        return self.client.rput("{method_name}", json=data)"""
        else:
            impl = f"""        return self.client.{sdk_method}(**kwargs)"""
    else:
        # Generate generic wrapper
        param_names = [p for p in parameters.keys()]
        if param_names:
            # Use proper Python dictionary syntax with quoted keys
            kwargs_dict = ", ".join([f'"{p}": {p}' for p in param_names])
            impl = f"""        kwargs = {{{kwargs_dict}}}
        kwargs = {{k: v for k, v in kwargs.items() if v is not None}}
        return self.client.{sdk_method}(**kwargs)"""
        else:
            impl = f"""        return self.client.{sdk_method}()"""
    
    return f'''    def {method_name}(
        {params_str}
    ) -> Dict[str, Any]:
        """{docstring}

        Args:
{params_doc}

        Returns:
            Dict[str, Any]: PagerDuty API response

        Reference:
            PagerDuty API method: {sdk_method}
        """
{impl}
'''


def generate_class_code(class_name: str, methods: List[Dict[str, Any]]) -> str:
    """Generate complete class code."""
    
    # Group methods by category
    categories: Dict[str, List[Dict[str, Any]]] = {}
    for method in methods:
        category = method.get("category", "General")
        if category not in categories:
            categories[category] = []
        categories[category].append(method)
    
    # Generate header
    header = f'''"""PagerDuty Data Source - Auto-generated from SDK introspection

This module provides comprehensive PagerDuty API access using the official pagerduty SDK (>=5.0.0).
Uses RestApiV2Client from the pagerduty package.

Auto-generated using SDK introspection to ensure complete API coverage.

Usage:
    from app.sources.client.pagerduty.pagerduty import PagerDutyClient
    from app.sources.external.pagerduty.pagerduty_data_source import {class_name}
    
    client = PagerDutyClient.build_with_config(config)
    datasource = {class_name}(client)
    
    # List incidents
    incidents = datasource.list_incidents()
    
    # Get specific incident
    incident = datasource.get_incident(incident_id="P123ABC")
"""

import logging
from typing import Any, Dict, List, Optional

from app.sources.client.pagerduty.pagerduty import PagerDutyClient

logger = logging.getLogger(__name__)


class {class_name}:
    """PagerDuty Data Source with comprehensive API coverage.
    
    Auto-generated from PagerDuty SDK (>=5.0.0) introspection.
    Uses RestApiV2Client for all API operations.
    Provides access to all major PagerDuty API endpoints.
    """
    
    def __init__(self, client: PagerDutyClient) -> None:
        """Initialize PagerDuty DataSource.
        
        Args:
            client: PagerDutyClient instance wrapping RestApiV2Client
        """
        self.client = client.get_sdk_client()

'''
    
    # Generate methods by category
    method_codes = []
    for category, category_methods in sorted(categories.items()):
        method_codes.append(f"\n    # {'=' * 70}")
        method_codes.append(f"    # {category.upper()} API ({len(category_methods)} methods)")
        method_codes.append(f"    # {'=' * 70}\n")
        
        for method in category_methods:
            method_codes.append(generate_method_code(method))
    
    # Add __all__
    footer = f"\n\n__all__ = ['{class_name}']\n"
    
    return header + "\n".join(method_codes) + footer


def main(argv: Optional[List[str]] = None) -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Generate PagerDuty DataSource from SDK")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output file path")
    parser.add_argument("--class-name", default=DEFAULT_CLASS, help="Generated class name")
    parser.add_argument("--test", action="store_true", help="Run in test mode")
    
    args = parser.parse_args(argv)
    
    if args.test:
        print("🧪 Test mode - would generate PagerDuty DataSource")
        return
    
    print("🚀 Generating PagerDuty DataSource from SDK introspection...")
    
    # Discover methods
    discoverer = PagerDutySDKMethodDiscoverer()
    methods = discoverer.discover_api_methods()
    
    print(f"📋 Discovered {len(methods)} PagerDuty API methods")
    
    # Generate code
    code = generate_class_code(args.class_name, methods)
    
    # Save to file
    script_dir = Path(__file__).parent
    pagerduty_dir = script_dir / "pagerduty"
    pagerduty_dir.mkdir(exist_ok=True)
    
    out_file = pagerduty_dir / args.out
    out_file.write_text(code, encoding="utf-8")
    
    print(f"✅ Generated: {out_file}")
    print(f"\n📊 Summary:")
    print(f"   Total methods: {len(methods)}")
    
    # Count by category
    categories: Dict[str, int] = {}
    for method in methods:
        cat = method.get("category", "General")
        categories[cat] = categories.get(cat, 0) + 1
    
    print(f"\n   Methods by category:")
    for cat, count in sorted(categories.items()):
        print(f"   • {cat}: {count} methods")
    
    print(f"\n💡 Usage Example:")
    print(f"""   from app.sources.client.pagerduty.pagerduty import PagerDutyClient
   from app.sources.external.pagerduty.{args.out.replace('.py', '')} import {args.class_name}
   
   client = PagerDutyClient.build_with_config(config)
   datasource = {args.class_name}(client)
   
   incidents = datasource.list_incidents()
   incident = datasource.get_incident(incident_id="P123ABC")
""")


if __name__ == "__main__":
    main()
