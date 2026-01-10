# ruff: noqa
#!/usr/bin/env python3
"""
Workday API Code Generator - Dynamic Fetching
==============================================
Fetches OpenAPI specs from Workday Community at runtime.
Generates comprehensive Workday DataSource covering 800+ endpoints.

Pattern inspired by Microsoft Graph generators.
"""

import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

# =============================================================================
# CONFIGURATION
# =============================================================================

# Workday Community OpenAPI spec base URL
WORKDAY_SPEC_BASE_URL = "https://community.workday.com/sites/default/files/file-hosting/restapi/"

# All Workday OpenAPI spec files (47 total)
WORKDAY_SPECS = [
    "absenceManagement_v3_20251206_oas2.json",
    "accountsPayable_v1_20251206_oas2.json",
    "attachments_v1_20251206_oas2.json",
    "benefitPartner_v1_20251206_oas2.json",
    "budgets_v1_20251206_oas2.json",
    "businessProcess_v1_20251206_oas2.json",
    "common_v1_20251206_oas2.json",
    "compensation_v2_20251206_oas2.json",
    "connect_v2_20251206_oas2.json",
    "contractCompliance_v1_20251206_oas2.json",
    "coreAccounting_v1_20251206_oas2.json",
    "customObjectDataMultiInstance_v2_20230712_oas2.json",
    "customObjectDataSingleInstance_v2_20230712_oas2.json",
    "customObjectDefinition_v1_20251206_oas2.json",
    "customerAccounts_v1_20251206_oas2.json",
    "expense_v1_20251206_oas2.json",
    "finTaxPublic_v1_20251206_oas2.json",
    "globalPayroll_v1_20251206_oas2.json",
    "graph_v1_20251206_oas2.json",
    "helpArticle_v1_20251206_oas2.json",
    "helpCase_v4_20251206_oas2.json",
    "holiday_v1_20251206_oas2.json",
    "journeys_v1_20251206_oas2.json",
    "learning_v1_20251206_oas2.json",
    "oAuthClient_v1_20251206_oas2.json",
    "payroll_v2_20251206_oas2.json",
    "performanceEnablement_v5_20251206_oas2.json",
    "person_v4_20251206_oas2.json",
    "prismAnalytics_v3_20231120_oas3.json",
    "privacy_v1_20251206_oas2.json",
    "procurement_v5_20251206_oas2.json",
    "projects_v1_20251206_oas2.json",
    "recruiting_v4_20251206_oas2.json",
    "request_v2_20251206_oas2.json",
    "revenue_v1_20251206_oas2.json",
    "staffing_v7_20251206_oas2.json",
    "studentAcademicFoundation_v1_20251206_oas2.json",
    "studentCore_v1_20251206_oas2.json",
    "studentCurriculum_v1_20251206_oas2.json",
    "studentEngagement_v1_20251206_oas2.json",
    "studentFinance_v1_20251206_oas2.json",
    "studentRecruiting_v1_20251206_oas2.json",
    "systemMetrics_v1_20251206_oas2.json",
    "talentManagement_v2_20251206_oas2.json",
    "timeTracking_v5_20251206_oas2.json",
    "worktag_v1_20251206_oas2.json",
    "wql_v1_20251206_oas2.json",
]

# Type Mappings
TYPE_MAPPING = {
    "string": "str",
    "integer": "int",
    "boolean": "bool",
    "number": "float",
    "array": "List",
    "object": "Dict",
}

# =============================================================================
# SPEC LOADING AND PARSING
# =============================================================================

def _read_bytes_from_url(url: str) -> bytes:
    """Read bytes from URL."""
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read()


def load_spec_from_url(url: str) -> Dict[str, Any]:
    """Load OpenAPI spec from Workday Community URL.
    
    Args:
        url: Workday Community URL to fetch from
    
    Returns:
        Parsed OpenAPI spec as dictionary
    """
    data = _read_bytes_from_url(url)
    return json.loads(data.decode("utf-8"))




def clean_param_name(name: str) -> str:
    """Clean parameter name to be a valid Python identifier."""
    name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    if name and name[0].isdigit():
        name = f"param_{name}"
    
    reserved = {"from", "class", "return", "pass", "import", "global", "yield", 
                "def", "for", "while", "if", "else", "elif", "try", "except", 
                "finally", "raise", "with", "as", "del", "in", "is", "not", 
                "or", "and", "break", "continue", "lambda", "nonlocal", "type", "object"}
    if name in reserved:
        return f"{name}_param"
    return name


def to_snake_case(name: str) -> str:
    """Convert to snake_case."""
    name = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', name).lower()


def generate_method_name(method: str, path: str) -> str:
    """Generate method name from HTTP method and path."""
    # Remove query parameters if present (e.g., ?type=archive)
    if '?' in path:
        path = path.split('?')[0]
    
    segments = [s for s in path.split('/') if s]
    static_segments = []
    
    for s in segments:
        # Skip path parameters (e.g., {id}, {subresource-id})
        if s.startswith('{') and s.endswith('}'):
            continue
        # Clean segment: remove special chars that aren't valid in Python identifiers
        cleaned = re.sub(r'[^a-zA-Z0-9_]', '_', s)
        static_segments.append(to_snake_case(cleaned))
    
    base_name = "_".join(static_segments)
    
    # Determine if last segment is a variable
    last_segment = segments[-1] if segments else ""
    is_variable = last_segment.startswith('{') and last_segment.endswith('}')

    if method.upper() == "GET":
        if is_variable:
            return f"get_{base_name}"
        else:
            return f"list_{base_name}"
    elif method.upper() == "POST":
        return f"create_{base_name}"
    elif method.upper() == "PUT":
        return f"update_{base_name}"
    elif method.upper() == "PATCH":
        return f"update_{base_name}"
    elif method.upper() == "DELETE":
        return f"delete_{base_name}"
        
    return f"{method.lower()}_{base_name}"


def parse_parameters(parameters: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Parse OpenAPI parameters into Python-friendly format."""
    parsed = {}
    
    for param in parameters:
        name = param.get("name")
        if not name:
            continue
            
        cleaned_name = clean_param_name(name)
        param_in = param.get("in")
        required = param.get("required", False)
        
        schema = param.get("schema", {})
        param_type = param.get("type")
        if not param_type and schema:
            param_type = schema.get("type")
            
        py_type = TYPE_MAPPING.get(param_type, "Any")
        
        if param_type == "array":
            items = param.get("items", {})
            item_type = items.get("type", "Any")
            py_type = f"List[{TYPE_MAPPING.get(item_type, 'Any')}]"
            
        if not required:
            py_type = f"Optional[{py_type}]"
            
        parsed[cleaned_name] = {
            'type': py_type,
            'location': param_in,
            'original_name': name,
            'description': param.get("description", "")
        }
            
    return parsed



def extract_path_parameters(path: str) -> List[str]:
    """Extract parameter names from path template (e.g., {id}, {customObjectAlias})."""
    import re
    return re.findall(r'\{([^}]+)\}', path)


def extract_endpoints_from_spec(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract endpoints from a single OpenAPI spec."""
    paths = spec.get("paths", {})
    base_path = spec.get("basePath", "")
    endpoints = []
    
    for path, methods in paths.items():
        full_path = f"{base_path}{path}"
        
        for verb, details in methods.items():
            if verb.lower() not in ["get", "post", "put", "patch", "delete"]:
                continue
                
            method_name = generate_method_name(verb, full_path)
            
            # Get parameters from OpenAPI spec
            params = details.get("parameters", [])
            parsed_params_dict = parse_parameters(params)
            
            # Extract path parameters from the path template itself
            path_param_names = extract_path_parameters(full_path)
            for param_name in path_param_names:
                # Convert to snake_case for Python
                cleaned = clean_param_name(param_name)
                # Only add if not already in parsed params
                if cleaned not in parsed_params_dict:
                    # Add as required string parameter
                    parsed_params_dict[cleaned] = {
                        'type': 'str',
                        'location': 'path',
                        'original_name': param_name,
                        'description': f'Path parameter {param_name}'
                    }
            
            required = [k for k, v in parsed_params_dict.items() 
                       if not v['type'].startswith('Optional')]

            endpoints.append({
                'name': method_name,
                'method': verb.upper(),
                'path': full_path,
                'description': details.get("summary", "No summary"),
                'parameters': parsed_params_dict,
                'required': required
            })
            
    return endpoints


def fetch_single_spec(spec_file: str, index: int, total: int) -> tuple[str, List[Dict[str, Any]], Optional[str]]:
    """Fetch and parse a single spec file. Returns (spec_file, endpoints, error)."""
    url = f"{WORKDAY_SPEC_BASE_URL}{spec_file}"
    try:
        spec = load_spec_from_url(url)
        endpoints = extract_endpoints_from_spec(spec)
        return (spec_file, endpoints, None)
    except Exception as e:
        return (spec_file, [], str(e))


def load_all_workday_specs() -> List[Dict[str, Any]]:
    """Load all Workday OpenAPI specs in parallel and extract endpoints."""
    all_endpoints = []
    processed_names = set(['get_client'])
    failed_specs = []
    
    print(f"Loading {len(WORKDAY_SPECS)} Workday OpenAPI specs in parallel...")
    
    # Fetch all specs concurrently
    with ThreadPoolExecutor(max_workers=10) as executor:
        # Submit all fetch tasks
        future_to_spec = {
            executor.submit(fetch_single_spec, spec_file, i+1, len(WORKDAY_SPECS)): spec_file
            for i, spec_file in enumerate(WORKDAY_SPECS)
        }
        
        # Process results as they complete
        completed = 0
        for future in as_completed(future_to_spec):
            spec_file, endpoints, error = future.result()
            completed += 1
            
            if error:
                print(f"  [{completed}/{len(WORKDAY_SPECS)}] ✗ {spec_file}: {error}")
                failed_specs.append(spec_file)
            else:
                # Deduplicate method names
                for ep in endpoints:
                    method_name = ep['name']
                    original_name = method_name
                    counter = 1
                    while method_name in processed_names:
                        method_name = f"{original_name}_{counter}"
                        counter += 1
                    processed_names.add(method_name)
                    ep['name'] = method_name
                    all_endpoints.append(ep)
                
                print(f"  [{completed}/{len(WORKDAY_SPECS)}] ✓ {spec_file}: {len(endpoints)} endpoints")
    
    print(f"\n✅ Total endpoints extracted: {len(all_endpoints)}")
    if failed_specs:
        print(f"⚠️  Failed to load {len(failed_specs)} specs: {', '.join(failed_specs)}")
    
    return all_endpoints


# =============================================================================
# CODE GENERATION
# =============================================================================

class WorkdayCodeGenerator:
    """Advanced Code Generator for Workday API."""
    
    HTTP_ERROR_THRESHOLD = 400

    def __init__(self, endpoints: List[Dict[str, Any]]):
        self.endpoints = endpoints

    def _get_type_hint(self, param_info: Dict[str, Any]) -> str:
        """Resolve python type hint, avoiding raw 'Any' where possible."""
        raw_type = param_info.get('type', 'Any')
        location = param_info.get('location')
        
        # Improve "Any" based on location
        if raw_type == "Any":
            if location == "body":
                return "Dict[str, Any]"
            elif location == "query":
                return "str"
        
        # Improve "Optional[Any]"
        if raw_type == "Optional[Any]":
            if location == "body":
                return "Optional[Dict[str, Any]]"
            elif location == "query":
                return "Optional[str]"
                
        return raw_type

    def _build_docstring(self, description: str, params: Dict[str, Any], 
                         sorted_keys: List[str]) -> str:
        """Build Zammad-style docstring."""
        lines = []
        safe_desc = description.replace('"', '\\"')
        lines.append(f'        """{safe_desc}')
        lines.append('')
        
        if sorted_keys:
            lines.append('        Args:')
            for key in sorted_keys:
                p_desc = params[key]['description'].replace('"', '\\"')
                is_optional = 'Optional' in params[key]['type']
                req_str = 'optional' if is_optional else 'required'
                lines.append(f'            {key}: {p_desc} ({req_str})')
            lines.append('')
            
        lines.append('        Returns:')
        lines.append('            WorkdayResponse')
        lines.append('        """')
        return "\n".join(lines)

    def _generate_method(self, ep: Dict[str, Any]) -> str:
        """Generate a single API method."""
        method_name = ep['name']
        description = ep['description']
        path = ep['path']
        verb = ep['method']
        params = ep['parameters']
        
        # Sort: required first, then optional
        sorted_param_keys = sorted(
            params.keys(), 
            key=lambda k: 'Optional' in params[k]['type']
        )
        
        # 1. Signature
        args_parts = ["self"]
        for p_name in sorted_param_keys:
            p_type = self._get_type_hint(params[p_name])
            if 'Optional' in p_type or 'Optional' in params[p_name]['type']:
                args_parts.append(f"{p_name}: {p_type} = None")
            else:
                args_parts.append(f"{p_name}: {p_type}")
        
        signature = f"    async def {method_name}(\n        " + ",\n        ".join(args_parts) + "\n    ) -> WorkdayResponse:"

        # 2. Docstring
        docstring = self._build_docstring(description, params, sorted_param_keys)
        
        # 3. URL Construction
        path_code = f'        url = f"{{self.base_url}}{path}"'
        path_params = [k for k,v in params.items() if v['location'] == 'path']
        for p_name in path_params:
            original = params[p_name]['original_name']
            path_code += f'.replace("{{{original}}}", str({p_name}))'

        # 4. Params & Body Construction
        setup_code = [
            '        params: Dict[str, Any] = {}',
            '        body: Dict[str, Any] = {}'
        ]
        
        for p_name in sorted_param_keys:
            p_info = params[p_name]
            original = p_info['original_name']
            location = p_info['location']
            
            check = f'if {p_name} is not None:'
            
            if location == 'query':
                block = [
                    f'        {check}',
                    f'            params["{original}"] = {p_name}'
                ]
                setup_code.extend(block)
            elif location == 'body':
                block = [
                    f'        {check}',
                    f'            if isinstance({p_name}, dict):',
                    f'                body.update({p_name})',
                    f'            else:',
                    f'                body["{original}"] = {p_name}'
                ]
                setup_code.extend(block)

        # 5. Execution Block
        exec_code = [
            '',
            '        try:',
            '            request = HTTPRequest(',
            '                url=url,',
            f'                method="{verb}",',
            '                headers={"Content-Type": "application/json"},',
            '                query_params=params,',
            '                body=body if body else None',
            '            )',
            '            response = await self.http_client.execute(request)',
            '',
            f'            success = response.status < HTTP_ERROR_THRESHOLD',
            '            return WorkdayResponse(',
            '                success=success,',
            '                data=response.json() if response.text else None,',
            f'                message=f"{method_name} {{\'succeeded\' if success else \'failed\'}}",',
            '                error=response.text if not success else None',
            '            )',
            '        except Exception as e:',
            '            return WorkdayResponse(success=False, error=str(e), message=str(e))'
        ]

        # Combine all parts
        return "\n".join([
            signature,
            docstring,
            path_code,
            "\n".join(setup_code),
            "\n".join(exec_code),
            ""
        ])

    def generate(self, output_path: str):
        """Generate the complete Workday DataSource."""
        print(f"\n🚀 Generating Workday API Client...")
        
        header = [
            '# Auto-generated Workday API Wrapper',
            '# Generated by WorkdayCodeGenerator (Dynamic Fetch)',
            '',
            'from typing import Any, Dict, List, Optional',
            '',
            'from app.sources.client.http.http_request import HTTPRequest',
            'from app.sources.client.workday import WorkdayClient, WorkdayResponse',
            '',
            '# HTTP status code threshold for determining success/failure',
            'HTTP_ERROR_THRESHOLD = 400',
            '',
            'class WorkdayDataSource:',
            '    """Workday API Data Source',
            '    ',
            '    Complete API wrapper covering 800+ endpoints.',
            '    Generated dynamically from Workday Community OpenAPI specs.',
            '    """',
            '',
            '    def __init__(self, workday_client: WorkdayClient) -> None:',
            '        self.http_client = workday_client.get_client()',
            '        self._workday_client = workday_client',
            '        self.base_url = workday_client.get_base_url().rstrip(\'/\')',
            '',
            '    def get_client(self) -> WorkdayClient:',
            '        return self._workday_client',
            ''
        ]

        methods = []
        for ep in self.endpoints:
            methods.append(self._generate_method(ep))
            
        full_content = "\n".join(header) + "\n" + "\n".join(methods)
        
        import os
        output_dir = os.path.dirname(output_path)
        if output_dir:  # Only create directory if path contains a directory
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, 'w') as f:
            f.write(full_content)
        
        print(f"✅ Generated client at {output_path} with {len(self.endpoints)} methods.")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def generate_workday_client(output_path: str):
    """Main generation function."""
    # Load all specs and extract endpoints
    endpoints = load_all_workday_specs()
    
    # Generate the client
    generator = WorkdayCodeGenerator(endpoints)
    generator.generate(output_path)


def main() -> int:
    """Main entry point."""
    import os
    
    # Output to workday folder in current directory
    output_path = os.path.join('workday', 'workday.py')
    
    try:
        generate_workday_client(output_path)
        return 0
    except Exception as e:
        print(f'❌ Error: {e}')
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
