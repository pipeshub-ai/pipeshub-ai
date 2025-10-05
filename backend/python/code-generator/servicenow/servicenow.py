# ruff: noqa
"""
Extensible OpenAPI to Python Data Source Generator

This framework allows you to generate Python data source classes from OpenAPI specs
with minimal configuration. Add new APIs by simply providing their spec files.
"""

import yaml # type: ignore
import sys
import argparse
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class ParameterInfo:
    """Metadata for an API parameter."""
    name: str
    location: str  # 'path', 'query', 'header', 'body'
    required: bool
    description: str
    param_type: str = 'Any'


@dataclass
class EndpointInfo:
    """Metadata for an API endpoint."""
    path: str
    method: str
    operation_id: str
    description: str
    parameters: List[ParameterInfo] = field(default_factory=list)
    path_params: List[str] = field(default_factory=list)
    query_params: List[ParameterInfo] = field(default_factory=list)
    has_request_body: bool = False


class OpenAPIParser:
    """Generic OpenAPI specification parser."""
    
    def __init__(self, spec_path: str):
        """Initialize parser with an OpenAPI spec file."""
        self.spec_path = Path(spec_path)
        with open(spec_path, 'r') as f:
            self.spec = yaml.safe_load(f)
        
        self.info = self.spec.get('info', {})
        self.servers = self.spec.get('servers', [])
        self.paths = self.spec.get('paths', {})
        self.base_url = self.servers[0]['url'].rstrip('/') if self.servers else ''
    
    def parse_endpoints(self) -> List[EndpointInfo]:
        """Parse all endpoints from the OpenAPI spec."""
        endpoints = []
        
        for path, path_item in self.paths.items():
            for method, operation in path_item.items():
                if method.lower() not in ['get', 'post', 'put', 'patch', 'delete', 'options', 'head']:
                    continue
                
                endpoint = self._parse_operation(path, method, operation)
                endpoints.append(endpoint)
        
        return endpoints
    
    def _parse_operation(self, path: str, method: str, operation: Dict) -> EndpointInfo:
        """Parse a single operation into an EndpointInfo object."""
        # Generate operation ID from path and method if not provided
        operation_id = operation.get('operationId', 
                                     self._generate_operation_id(path, method))
        
        description = operation.get('description', operation.get('summary', ''))
        
        # Parse parameters
        parameters = []
        path_params = []
        query_params = []
        
        for param in operation.get('parameters', []):
            param_info = ParameterInfo(
                name=param['name'],
                location=param['in'],
                required=param.get('required', False),
                description=param.get('description', ''),
                param_type=self._infer_type(param.get('schema', {}))
            )
            
            parameters.append(param_info)
            
            if param['in'] == 'path':
                path_params.append(param['name'])
            elif param['in'] == 'query':
                query_params.append(param_info)
        
        has_request_body = 'requestBody' in operation
        
        return EndpointInfo(
            path=path,
            method=method.upper(),
            operation_id=operation_id,
            description=description,
            parameters=parameters,
            path_params=path_params,
            query_params=query_params,
            has_request_body=has_request_body
        )
    
    def _generate_operation_id(self, path: str, method: str) -> str:
        """Generate a readable operation ID from path and method."""
        # Convert /api/now/table/{tableName} to table_tableName
        parts = [p for p in path.split('/') if p and not p.startswith('{')]
        param_parts = [p.strip('{}') for p in path.split('/') if p.startswith('{')]
        
        name_parts = parts[-2:] if len(parts) >= 2 else parts
        name = '_'.join(name_parts + param_parts)
        
        return f"{method.lower()}_{name}"
    
    def _infer_type(self, schema: Dict) -> str:
        """Infer Python type from OpenAPI schema."""
        if not schema:
            return 'Any'
        
        type_map = {
            'string': 'str',
            'integer': 'int',
            'number': 'float',
            'boolean': 'bool',
            'array': 'List',
            'object': 'Dict'
        }
        
        return type_map.get(schema.get('type', 'Any'), 'Any')


class PythonCodeGenerator:
    """Generates Python code for data source classes."""
    
    def __init__(self, class_name: str, base_url: str, endpoints: List[EndpointInfo],
                 auth_type: str = 'basic'):
        """
        Initialize code generator.
        
        Args:
            class_name: Name of the generated class
            base_url: Base URL for the API
            endpoints: List of endpoint information
            auth_type: Authentication type ('basic', 'bearer', 'apikey', 'none')
        """
        self.class_name = class_name
        self.base_url = base_url
        self.endpoints = endpoints
        self.auth_type = auth_type
    
    def generate(self) -> str:
        """Generate complete Python class code."""
        parts = [
            self._generate_imports(),
            self._generate_class_header(),
            self._generate_init_method(),
            self._generate_helper_methods(),
            self._generate_endpoint_methods()
        ]
        
        return '\n\n'.join(parts)
    
    def _generate_imports(self) -> str:
        """Generate import statements."""
        return '''from typing import Dict, Optional, Any
from app.sources.client.http.http_request import HTTPRequest
from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.servicenow.servicenow import ServiceNowClient, ServiceNowResponse'''
    
    def _generate_class_header(self) -> str:
        """Generate class docstring and definition."""
        return f'''class {self.class_name}:
    """
    Auto-generated data source for ServiceNow API operations.
    Base URL: {self.base_url}
    This class combines all ServiceNow API endpoints from multiple OpenAPI specifications.
    """'''
    
    def _generate_init_method(self) -> str:
        """Generate __init__ method based on auth type."""
        if self.auth_type == 'basic':
            return '''    def __init__(self, client: ServiceNowClient):
        """
        Initialize the data source.
        Args:
            client: ServiceNow client instance
        """
        self.client = client.get_client()
        self.base_url = client.get_base_url()'''
        
        elif self.auth_type == 'bearer':
            return '''    def __init__(self, client: ServiceNowClient):
        """
        Initialize the data source.

        Args:
            client: ServiceNow client instance
        """
        self.client = client.get_client()
        self.base_url = client.get_base_url()'''
        
        elif self.auth_type == 'apikey':
            return '''    def __init__(self, client: ServiceNowClient):
        """
        Initialize the data source.
        
        Args:
            client: ServiceNow client instance
        """
        self.client = client.get_client()
        self.base_url = client.get_base_url()'''
        
        else:  # 'none'
            return '''    def __init__(self, client: ServiceNowClient):
        """
        Initialize the data source.
        
        Args:
            client: ServiceNow client instance
        """
        self.client = client.get_client()
        self.base_url = client.get_base_url()'''
    
    def _generate_helper_methods(self) -> str:
        """Generate helper methods."""
        return '''    def _build_url(self, path: str) -> str:
        """Build full URL from path."""
        return f"{self.base_url}{path}"
    
    def _build_params(self, **kwargs) -> Dict[str, Any]:
        """Build query parameters, filtering out None values."""
        return {k: v for k, v in kwargs.items() if v is not None}
    
    async def _handle_response(self, response) -> ServiceNowResponse:
        """Handle API response and return ServiceNowResponse."""
        try:
            if response.status >= HttpStatusCode.BAD_REQUEST.value:
                return ServiceNowResponse(
                    success=False,
                    error=f"HTTP {response.status}",
                    message=response.text
                )
            
            data = response.json() if response.text else {}
            return ServiceNowResponse(
                success=True,
                data=data
            )
        except Exception as e:
            return ServiceNowResponse(
                success=False,
                error=str(e),
                message="Failed to parse response"
            )'''
    
    def _generate_endpoint_methods(self) -> str:
        """Generate methods for all endpoints."""
        methods = []
        
        for endpoint in self.endpoints:
            method_code = self._generate_endpoint_method(endpoint)
            methods.append(method_code)
        
        return '\n\n'.join(methods)
    
    def _generate_endpoint_method(self, endpoint: EndpointInfo) -> str:
        """Generate a single endpoint method."""
        method_name = self._sanitize_method_name(endpoint.operation_id)
        
        # Build parameter list
        params = []
        for path_param in endpoint.path_params:
            params.append(f"{path_param}: str")
        
        if endpoint.has_request_body:
            params.append("data: Dict[str, Any]")
        
        for query_param in endpoint.query_params:
            param_type = query_param.param_type if query_param.param_type else 'str'
            if param_type == 'Any':
                param_type = 'str'
            if not query_param.required:
                params.append(f"{query_param.name}: Optional[{param_type}] = None")
            else:
                params.append(f"{query_param.name}: {param_type}")
        
        # Format parameters with each on its own line
        if len(params) > 0:
            all_params = ['self'] + params
            params_formatted = ',\n        '.join(all_params)
            method_signature = f'    async def {method_name}(\n        {params_formatted}\n    ) -> ServiceNowResponse:'
        else:
            method_signature = f'    async def {method_name}(self) -> ServiceNowResponse:'
        
        # Build docstring
        docstring = self._generate_docstring(endpoint)
        
        # Build URL path
        url_path = endpoint.path
        for path_param in endpoint.path_params:
            url_path = url_path.replace(f"{{{path_param}}}", f"{{{path_param}}}")
        
        # Build method body
        body_lines = [
            method_signature,
            f'        """{docstring}"""',
            f'        url = self._build_url(f"{url_path}")'
        ]
        
        # Add query parameters if present
        if endpoint.query_params:
            query_param_names = [p.name for p in endpoint.query_params]
            query_dict = ', '.join([f'{name}={name}' for name in query_param_names])
            body_lines.append(f'        params = self._build_params({query_dict})')
        else:
            body_lines.append('        params = {}')
        
        # Build request using HTTPRequest
        body_lines.append('')
        
        # Add request call
        method_upper = endpoint.method.upper()
        if endpoint.has_request_body:
            body_lines.append(f'        request = HTTPRequest(')
            body_lines.append(f'            method="{method_upper}",')
            body_lines.append(f'            url=url,')
            body_lines.append(f'            headers=self.client.headers,')
            body_lines.append(f'            query_params=params,')
            body_lines.append(f'            body=data')
            body_lines.append(f'        )')
        else:
            body_lines.append(f'        request = HTTPRequest(')
            body_lines.append(f'            method="{method_upper}",')
            body_lines.append(f'            url=url,')
            body_lines.append(f'            headers=self.client.headers,')
            body_lines.append(f'            query_params=params')
            body_lines.append(f'        )')
        
        body_lines.append('        response = await self.client.execute(request)')
        body_lines.append('        return await self._handle_response(response)')
        
        return '\n'.join(body_lines)
    
    def _generate_docstring(self, endpoint: EndpointInfo) -> str:
        """Generate method docstring."""
        desc = ' '.join(endpoint.description.split())
        
        lines = [desc, 'Args:']
        for path_param in endpoint.path_params:
            lines.append(f'    {path_param}: Path parameter')
        if endpoint.has_request_body:
            lines.append('    data: Request body data')
        for query_param in endpoint.query_params:
            lines.append(f'    {query_param.name}: {query_param.description}')
        lines.append('Returns:')
        lines.append('    ServiceNowResponse object with success status and data/error')
        
        return '\n        '.join(lines)
    
    def _sanitize_method_name(self, name: str) -> str:
        """Sanitize method name to be valid Python identifier."""
        # Replace invalid characters
        name = name.replace('-', '_').replace('.', '_')
        # Ensure it doesn't start with a number
        if name[0].isdigit():
            name = f'_{name}'
        return name


class ConsolidatedAPIGenerator:
    """
    Generator that combines multiple OpenAPI specifications into a single data source class.
    
    To add a new API, simply provide the spec file path.
    All methods will be combined into one ServiceNowDataSource class.
    """
    
    def __init__(self, class_name: str = "ServiceNowDataSource", auth_type: str = 'basic'):
        """Initialize the consolidated API generator.
        
        Args:
            class_name: Name of the generated class (default: ServiceNowDataSource)
            auth_type: Authentication type for all APIs
        """
        self.class_name = class_name
        self.auth_type = auth_type
        self.spec_paths: List[str] = []
        self.all_endpoints: List[EndpointInfo] = []
        self.base_url: str = ''
        self.generation_stats: Dict[str, Any] = {}
    
    def add_spec(self, spec_path: str) -> 'ConsolidatedAPIGenerator':
        """Add an OpenAPI spec file to combine.
        
        Args:
            spec_path: Path to OpenAPI specification file
            
        Returns:
            Self for method chaining
        """
        self.spec_paths.append(spec_path)
        return self
    
    def generate(self) -> str:
        """
        Generate a single data source class combining all API specs.
        
        Returns:
            Generated Python code as string
        """
        # Parse all specs and collect endpoints
        for spec_path in self.spec_paths:
            parser = OpenAPIParser(spec_path)
            endpoints = parser.parse_endpoints()
            self.all_endpoints.extend(endpoints)
            
            # Use base URL from first spec
            if not self.base_url:
                self.base_url = parser.base_url
        
        # Generate code with all endpoints combined
        generator = PythonCodeGenerator(
            class_name=self.class_name,
            base_url=self.base_url,
            endpoints=self.all_endpoints,
            auth_type=self.auth_type
        )
        
        code = generator.generate()
        
        # Collect stats
        self.generation_stats = {
            'total_endpoints': len(self.all_endpoints),
            'total_specs': len(self.spec_paths),
            'methods': {}
        }
        for endpoint in self.all_endpoints:
            self.generation_stats['methods'][endpoint.method] = \
                self.generation_stats['methods'].get(endpoint.method, 0) + 1
        
        return code
    
    def save_to_file(self, output_dir: str = "servicenow", 
                     filename: str = "servicenow_data_source.py"):
        """Save generated class to a file.
        
        Args:
            output_dir: Directory to save the file
            filename: Name of the output file
        """
        # Generate the code first
        code = self.generate()
        
        # Create output directory
        script_dir = Path(__file__).parent if __file__ else Path('.')
        output_path = script_dir / output_dir
        output_path.mkdir(exist_ok=True)
        
        # Set the full file path
        full_path = output_path / filename
        
        # Write the file
        full_path.write_text(code, encoding='utf-8')
        
        # Print status messages
        print(f"✅ Generated {self.class_name} with {self.generation_stats['total_endpoints']} endpoints")
        print(f"   from {self.generation_stats['total_specs']} OpenAPI specifications")
        print(f"📁 Saved to: {full_path}")
        
        print(f"\n📊 Summary:")
        print(f"   - Total endpoints: {self.generation_stats['total_endpoints']}")
        print(f"   - HTTP methods:")
        for method, count in sorted(self.generation_stats['methods'].items()):
            print(f"     * {method}: {count} endpoints")


def process_servicenow_apis(
    spec_files: List[str],
    class_name: str = "ServiceNowDataSource",
    output_dir: str = "servicenow",
    filename: str = "servicenow_data_source.py"
) -> None:
    """End-to-end pipeline for ServiceNow API generation.
    
    Args:
        spec_files: List of paths to OpenAPI spec files
        class_name: Name of the generated class
        output_dir: Directory to save the output
        filename: Name of the output file
    """
    print(f"🚀 Starting ServiceNow API data source generation...")
    print(f"📋 Processing {len(spec_files)} OpenAPI specification(s)...")
    
    # Handle relative paths by making them relative to script directory
    script_dir = Path(__file__).parent if __file__ else Path('.')
    
    resolved_specs = []
    for spec_file in spec_files:
        if not Path(spec_file).is_absolute():
            resolved_specs.append(str(script_dir / spec_file))
        else:
            resolved_specs.append(spec_file)
    
    # Create generator and add all specs
    generator = ConsolidatedAPIGenerator(class_name=class_name, auth_type="basic")
    
    for spec_file in resolved_specs:
        print(f"   - {Path(spec_file).name}")
        generator.add_spec(spec_file)
    
    try:
        print("\n⚙️  Analyzing OpenAPI specifications and generating methods...")
        generator.save_to_file(output_dir=output_dir, filename=filename)
        
        print(f"\n📂 File generated in: {script_dir / output_dir}")
        print(f"\n🎉 Successfully generated {class_name}!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise


def main():
    """Main function for ServiceNow API generator."""
    parser = argparse.ArgumentParser(
        description='Generate consolidated ServiceNow data source from multiple OpenAPI specs'
    )
    parser.add_argument(
        'specs',
        nargs='+',
        help='Paths to OpenAPI spec files'
    )
    parser.add_argument(
        '--class-name', '-c',
        default='ServiceNowDataSource',
        help='Name of the generated class (default: ServiceNowDataSource)'
    )
    parser.add_argument(
        '--output-dir', '-o',
        default='servicenow',
        help='Output directory (default: servicenow)'
    )
    parser.add_argument(
        '--filename', '-f',
        default='servicenow_data_source.py',
        help='Output filename (default: servicenow_data_source.py)'
    )
    
    args = parser.parse_args()
    
    try:
        process_servicenow_apis(
            spec_files=args.specs,
            class_name=args.class_name,
            output_dir=args.output_dir,
            filename=args.filename
        )
        return 0
    except Exception as e:
        print(f"Failed to generate ServiceNow data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())