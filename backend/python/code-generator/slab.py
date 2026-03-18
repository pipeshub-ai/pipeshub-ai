# ruff: noqa
"""
Slab GraphQL Data Source Generator
Generates wrapper methods for Slab GraphQL operations.
Creates a comprehensive Slab data source with query and mutation operations.

Slab uses a GraphQL API exclusively.
API Docs: https://slab.com/api/

Usage:
    python code-generator/slab.py
    python code-generator/slab.py --filename slab.py
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional

from app.sources.client.slab.graphql_op import SlabGraphQLOperations


class SlabDataSourceGenerator:
    """Generate Slab data source methods from GraphQL operations."""

    def __init__(self):
        """Initialize the Slab data source generator."""
        self.generated_methods = []
        self.operations = SlabGraphQLOperations.get_all_operations()

        # Define comprehensive Slab operations
        self.comprehensive_operations = self._define_comprehensive_operations()

    def _define_comprehensive_operations(self) -> Dict:
        """Define the full set of Slab GraphQL operations with parameters."""
        return {
            "queries": {
                "organization": {
                    "description": "Get organization information",
                    "parameters": {},
                    "example_usage": "await slab_datasource.organization()",
                },
                "users": {
                    "description": "List all users in the organization",
                    "parameters": {},
                    "example_usage": "await slab_datasource.users()",
                },
                "posts": {
                    "description": "List posts with optional status filter",
                    "parameters": {
                        "status": {
                            "type": "Optional[str]",
                            "required": False,
                            "description": "Post status filter (e.g. PUBLISHED)",
                        },
                    },
                    "example_usage": 'await slab_datasource.posts(status="PUBLISHED")',
                },
                "post": {
                    "description": "Get a single post by ID",
                    "parameters": {
                        "id": {"type": "str", "required": True, "description": "Post ID"},
                    },
                    "example_usage": 'await slab_datasource.post(id="post-id")',
                },
                "topics": {
                    "description": "List all topics",
                    "parameters": {},
                    "example_usage": "await slab_datasource.topics()",
                },
                "topic": {
                    "description": "Get a single topic by ID with its posts",
                    "parameters": {
                        "id": {"type": "str", "required": True, "description": "Topic ID"},
                    },
                    "example_usage": 'await slab_datasource.topic(id="topic-id")',
                },
                "searchPosts": {
                    "description": "Search posts by query string",
                    "parameters": {
                        "query": {
                            "type": "str",
                            "required": True,
                            "description": "Search query string",
                        },
                    },
                    "example_usage": 'await slab_datasource.searchPosts(query="search term")',
                },
            },
            "mutations": {
                "syncPost": {
                    "description": "Create or update a post via sync",
                    "parameters": {
                        "input": {
                            "type": "Dict[str, Any]",
                            "required": True,
                            "description": "Sync post input object",
                        },
                    },
                    "example_usage": 'await slab_datasource.syncPost(input={"title": "..."})',
                },
            },
        }

    def _generate_method(
        self,
        operation_name: str,
        operation_type: str,
        operation_info: Dict,
    ) -> str:
        """Generate a single data source method."""
        params = operation_info.get("parameters", {})
        description = operation_info.get("description", "")

        # Build method signature
        sig_parts = ["self"]
        for param_name, param_info in params.items():
            ptype = param_info.get("type", "str")
            if param_info.get("required", False):
                sig_parts.append(f"{param_name}: {ptype}")
            else:
                sig_parts.append(f"{param_name}: {ptype} = None")

        signature = ",\n        ".join(sig_parts)

        # Build variables dict
        var_lines = []
        for param_name, param_info in params.items():
            if param_info.get("required", False):
                var_lines.append(f'            variables["{param_name}"] = {param_name}')
            else:
                var_lines.append(f"            if {param_name} is not None:")
                var_lines.append(f'                variables["{param_name}"] = {param_name}')

        variables_block = "\n".join(var_lines) if var_lines else ""

        # Build docstring args
        args_doc = ""
        if params:
            args_doc = "\n\n        Args:"
            for param_name, param_info in params.items():
                args_doc += f"\n            {param_name}: {param_info.get('description', '')}"

        method = f"""    async def {operation_name}(
        {signature}
    ) -> GraphQLResponse:
        \"\"\"{description}{args_doc}
        \"\"\"
        query = SlabGraphQLOperations.get_operation_with_fragments("{operation_type}", "{operation_name}")
        variables: Dict[str, Any] = {{}}
{variables_block}

        try:
            response = await self._slab_client.get_client().execute(
                query=query, variables=variables, operation_name="{operation_name}"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute {operation_type} {operation_name}: {{str(e)}}")
"""
        self.generated_methods.append({
            "name": operation_name,
            "type": operation_type,
            "description": description,
        })
        return method

    def generate_slab_datasource(self) -> str:
        """Generate the complete Slab datasource class."""
        lines = [
            'from typing import Any, Dict, Optional',
            '',
            'from app.sources.client.graphql.response import GraphQLResponse',
            'from app.sources.client.slab.graphql_op import SlabGraphQLOperations',
            'from app.sources.client.slab.slab import (',
            '    SlabClient,',
            ')',
            '',
            '',
            'class SlabDataSource:',
            '    """',
            '    Slab GraphQL API client wrapper',
            '    Auto-generated wrapper for Slab GraphQL operations.',
            '    This class provides unified access to all Slab GraphQL operations while',
            '    maintaining proper typing and error handling.',
            '',
            '    Coverage:',
            '    - Organization info',
            '    - Users listing',
            '    - Posts (list, get, search)',
            '    - Topics (list, get)',
            '    - Mutations (syncPost)',
            '    """',
            '',
            '    def __init__(self, slab_client: SlabClient) -> None:',
            '        """',
            '        Initialize the Slab GraphQL data source.',
            '        Args:',
            '            slab_client (SlabClient): Slab client instance',
            '        """',
            '        self._slab_client = slab_client',
            '',
            '    # =============================================================================',
            '    # QUERY OPERATIONS',
            '    # =============================================================================',
            '',
        ]

        # Generate query methods
        for op_name, op_info in self.comprehensive_operations["queries"].items():
            lines.append(self._generate_method(op_name, "query", op_info))

        lines.append('    # =============================================================================')
        lines.append('    # MUTATION OPERATIONS')
        lines.append('    # =============================================================================')
        lines.append('')

        # Generate mutation methods
        for op_name, op_info in self.comprehensive_operations["mutations"].items():
            lines.append(self._generate_method(op_name, "mutation", op_info))

        return "\n".join(lines)

    def save_to_file(self, filename: Optional[str] = None) -> None:
        """Generate and save the Slab datasource to a file."""
        if filename is None:
            filename = "slab.py"

        script_dir = Path(__file__).parent if __file__ else Path(".")
        slab_dir = script_dir.parent / "app" / "sources" / "external" / "slab"
        slab_dir.mkdir(parents=True, exist_ok=True)

        full_path = slab_dir / filename

        class_code = self.generate_slab_datasource()
        full_path.write_text(class_code, encoding="utf-8")

        print(f"Generated Slab data source with {len(self.generated_methods)} methods")
        print(f"Saved to: {full_path}")

        # Print summary
        queries = sum(1 for m in self.generated_methods if m["type"] == "query")
        mutations = sum(1 for m in self.generated_methods if m["type"] == "mutation")
        print(f"\nMethods by type:")
        print(f"  - Queries: {queries}")
        print(f"  - Mutations: {mutations}")


def main():
    """Main function for Slab data source generator."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Slab GraphQL data source"
    )
    parser.add_argument("--filename", "-f", help="Output filename (optional)")

    args = parser.parse_args()

    try:
        generator = SlabDataSourceGenerator()
        generator.save_to_file(args.filename)
        return 0
    except Exception as e:
        print(f"Failed to generate Slab data source: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
