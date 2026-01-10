# ruff: noqa
import json
import re
import argparse
import keyword
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

class BitbucketCodeGenerator:
    def __init__(self, spec_path: str, class_name: str):
        self.spec_path = Path(spec_path)
        self.class_name = class_name
        self.spec = {}
        self.generated_methods: Set[str] = set()

    def load_spec(self):
        if not self.spec_path.exists():
            raise FileNotFoundError(f"Spec file not found at {self.spec_path}")
        
        with open(self.spec_path, "r", encoding="utf-8") as f:
            self.spec = json.load(f)

    def to_snake_case(self, name: str) -> str:
        """Converts camelCase or PascalCase to snake_case."""
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    def sanitize_name(self, name: str) -> str:
        """Sanitizes a string to be a valid Python identifier."""
        # Replace invalid chars with underscore
        name = name.replace("-", "_").replace(".", "_").replace("/", "_")
        # Remove anything else
        name = re.sub(r'[^a-zA-Z0-9_]', '', name)
        # Handle multiple underscores
        name = re.sub(r'_+', '_', name).strip('_')
        # Handle keywords
        if keyword.iskeyword(name) or name in ["self", "cls"]:
            name = f"{name}_param"
        # Handle starting with number
        if name and name[0].isdigit():
            name = f"v_{name}"
        return name

    def get_type_hint(self, schema: Dict) -> str:
        """Maps OpenAPI types to Python type hints."""
        t = schema.get("type")
        if t == "integer":
            return "int"
        elif t == "boolean":
            return "bool"
        elif t == "array":
            items = schema.get("items", {})
            return f"List[{self.get_type_hint(items)}]"
        elif t == "object":
            return "Dict[str, Any]"
        return "str"

    def parse_parameters(self, parameters: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
        """Separates parameters into path args (required) and query args (optional)."""
        path_args = []
        query_args = []

        for param in parameters:
            if "$ref" in param:
                continue 
            
            name = param.get("name")
            if not name:
                continue

            # Sanitize the name for use as a Python variable
            py_name = self.sanitize_name(name)
            
            param_in = param.get("in")
            schema = param.get("schema", {})
            type_hint = self.get_type_hint(schema)
            desc = param.get("description", "").replace("\n", " ").strip()

            param_data = {
                "original_name": name,
                "py_name": py_name,
                "type": type_hint,
                "description": desc,
                "required": param.get("required", False)
            }

            if param_in == "path":
                param_data["required"] = True
                path_args.append(param_data)
            elif param_in == "query":
                query_args.append(param_data)

        return path_args, query_args

    def generate_method_code(self, path: str, method: str, operation: Dict) -> str:
        # 1. Determine Method Name
        op_id = operation.get("operationId")
        if op_id:
            raw_method_name = self.to_snake_case(op_id)
        else:
            # Fallback: verb + path
            clean_path = path.strip("/").replace("/", "_").replace("{", "").replace("}", "").replace("-", "_")
            raw_method_name = f"{method}_{clean_path}"

        # Sanitize the method name (removes dots, dashes, etc)
        method_name = self.sanitize_name(raw_method_name)

        # Ensure uniqueness in class
        original_method_name = method_name
        counter = 1
        while method_name in self.generated_methods:
            method_name = f"{original_method_name}_{counter}"
            counter += 1
        self.generated_methods.add(method_name)

        # 2. Process Parameters
        # Merge path-level and operation-level parameters
        path_item_params = self.spec.get("paths", {}).get(path, {}).get("parameters", [])
        op_params = operation.get("parameters", [])
        all_params = path_item_params + op_params
        
        path_args, query_args = self.parse_parameters(all_params)

        # 3. Check for Request Body
        has_body = "requestBody" in operation
        
        # 4. Build Signature
        args_list = ["self"]
        
        # Path args (Required)
        for arg in path_args:
            args_list.append(f"{arg['py_name']}: {arg['type']}")
        
        # Body (Explicit)
        if has_body:
            args_list.append("body: Dict[str, Any]")

        # Query args (Optional)
        for arg in query_args:
            if arg['required']:
                 args_list.append(f"{arg['py_name']}: {arg['type']}")
            else:
                 args_list.append(f"{arg['py_name']}: Optional[{arg['type']}] = None")

        signature = f"    async def {method_name}({', '.join(args_list)}) -> BitbucketResponse:"

        # 5. Build Docstring
        summary = operation.get("summary", "").replace('"', "'").replace("\n", " ")
        description = operation.get("description", "").replace('"', "'")
        
        docstring = [f'        """{summary}']
        if description and description != summary:
            # Truncate long descriptions to keep file size manageable
            docstring.append(f"        {description[:200]}..." if len(description) > 200 else f"        {description}")
        
        docstring.append("")
        docstring.append("        Args:")
        
        all_args_for_doc = path_args + ([{'py_name': 'body', 'type': 'Dict', 'description': 'Request body payload'}] if has_body else []) + query_args
        
        for arg in all_args_for_doc:
            desc_trunc = arg['description'][:100].replace("\n", " ") + "..." if len(arg['description']) > 100 else arg['description']
            docstring.append(f"            {arg['py_name']}: {desc_trunc}")
            
        docstring.append("")
        docstring.append("        Returns:")
        docstring.append("            BitbucketResponse: API response")
        docstring.append('        """')

        # 6. Build Implementation
        lines = []
        
        # Path construction
        # Replace {original-param-name} in path with {sanitized_py_variable_name}
        safe_path = path
        for arg in path_args:
            safe_path = safe_path.replace(f"{{{arg['original_name']}}}", f"{{{arg['py_name']}}}")
        
        lines.append(f'        path = f"{safe_path}"')
        
        # Query Params Dictionary
        if query_args:
            lines.append('        params = {}')
            for arg in query_args:
                lines.append(f'        if {arg["py_name"]} is not None:')
                # The key in the 'params' dict must be the ORIGINAL name (e.g. 'q', 'sort', 'created-on')
                lines.append(f'            params["{arg["original_name"]}"] = {arg["py_name"]}')

        # HTTP Request
        lines.append('        ')
        lines.append('        try:')
        
        req_constructor = [
            f'url=self.base_url + path',
            f'method="{method.upper()}"',
            'headers={"Content-Type": "application/json"}'
        ]
        
        if query_args:
            req_constructor.append('query_params=params')
        if has_body:
            req_constructor.append('body=body')

        lines.append(f'            request = HTTPRequest(')
        for item in req_constructor:
            lines.append(f'                {item},')
        lines.append(f'            )')
        
        lines.append('            response = await self.client.execute(request)')
        lines.append('            return BitbucketResponse(')
        lines.append('                success=response.status < HTTP_ERROR_THRESHOLD,')
        lines.append('                data=response.json() if response.text() else {},')
        lines.append('                message=f"Request finished with status {response.status}"')
        lines.append('            )')
        lines.append('        except Exception as e:')
        lines.append('            return BitbucketResponse(success=False, error=str(e))')

        return signature + "\n" + "\n".join(docstring) + "\n" + "\n".join(lines)

    def generate_class(self) -> str:
        self.load_spec()
        
        content = [
            '# ruff: noqa',
            '"""',
            'Bitbucket API DataSource',
            'Auto-generated from OpenAPI Specification.',
            '"""',
            '',
            'from typing import Any, Dict, List, Optional, Union',
            'from app.sources.client.http.http_request import HTTPRequest',
            'from app.sources.client.bitbucket.bitbucket import BitbucketClient',
            'HTTP_ERROR_THRESHOLD = 400',
            '',
            'class BitbucketResponse:',
            '    """Standardized response wrapper."""',
            '    def __init__(self, success: bool, data: object = None, error: str = None, message: str = None):',
            '        self.success = success',
            '        self.data = data',
            '        self.error = error',
            '        self.message = message',
            '',
            f'class {self.class_name}:',
            '    """',
            '    Bitbucket API Data Source.',
            '    """',
            '    def __init__(self, client: BitbucketClient):',
            '        self.client = client.get_client()',
            '        self.base_url = client.get_base_url()',  # FIXED: Dynamic base URL
            ''
        ]

        paths = self.spec.get("paths", {})
        for path, methods in paths.items():
            for method_name, operation in methods.items():
                if method_name in ["get", "post", "put", "delete", "patch"]:
                    code = self.generate_method_code(path, method_name, operation)
                    content.append(code)
                    content.append("")

        return "\n".join(content)

def main():
    parser = argparse.ArgumentParser(description="Generate Bitbucket API Client")
    parser.add_argument("--spec", default="bitbucket.json", help="Path to OpenAPI spec")
    parser.add_argument("--out", default="bitbucket_data_source.py", help="Output file")
    args = parser.parse_args()

    print(f"🚀 Parsing {args.spec}...")
    try:
        generator = BitbucketCodeGenerator(args.spec, "BitbucketDataSource")
        code = generator.generate_class()

        with open(args.out, "w", encoding="utf-8") as f:
            f.write(code)
        
        print(f"✅ Generated {args.out} successfully! ({len(generator.generated_methods)} methods)")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    main()