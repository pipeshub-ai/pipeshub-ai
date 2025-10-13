# ruff: noqa
"""
Dropbox SDK API Method Generator
Generates wrapper methods by introspecting the official Dropbox SDK.
Uses actual SDK methods, parameters, and return types.
"""

import inspect
import re
from typing import Dict, Optional, Tuple, Union
from pathlib import Path
import dropbox # type: ignore


class DropboxSDKMethodGenerator:
    """Generate Dropbox API methods by introspecting the official SDK."""
    
    def __init__(self):
        """Initialize the Dropbox SDK generator."""
        self.generated_methods = []
        self.sdk_modules = self._get_sdk_modules()
        self.type_mappings = self._create_type_mappings()
        
    def _create_type_mappings(self) -> Dict[str, str]:
        """Create mappings from common types to specific Dropbox types."""
        return {
            # Basic types
            'Any': 'Union[str, int, bool, float]',
            'object': 'Union[str, int, bool, float]',
            
            # Dropbox specific return types
            'FileMetadata': 'files.FileMetadata',
            'FolderMetadata': 'files.FolderMetadata',
            'DeletedMetadata': 'files.DeletedMetadata',
            'Metadata': 'Union[files.FileMetadata, files.FolderMetadata, files.DeletedMetadata]',
            'ListFolderResult': 'files.ListFolderResult',
            'UploadSessionCursor': 'files.UploadSessionCursor',
            'CommitInfo': 'files.CommitInfo',
            'WriteMode': 'files.WriteMode',
            'SearchResult': 'files.SearchResult',
            'ThumbnailFormat': 'files.ThumbnailFormat',
            'ThumbnailSize': 'files.ThumbnailSize',
            
            # User types
            'FullAccount': 'users.FullAccount',
            'BasicAccount': 'users.BasicAccount',
            'SpaceUsage': 'users.SpaceUsage',
            
            # Sharing types
            'SharedLinkMetadata': 'sharing.SharedLinkMetadata',
            'SharedFolderMetadata': 'sharing.SharedFolderMetadata',
            'FolderPermission': 'sharing.FolderPermission',
            
            # Team types (when available)
            'TeamInfo': 'team.TeamInfo',
            'MemberInfo': 'team.MemberInfo',
            
            # Common collections
            'List[Any]': 'List[Union[str, int, bool]]',
            'Dict[str, Any]': 'Dict[str, Union[str, int, bool]]',
            'Dict[Any, Any]': 'Dict[str, Union[str, int, bool]]',
            
            # Specific collections
            'List[FileMetadata]': 'List[files.FileMetadata]',
            'List[FolderMetadata]': 'List[files.FolderMetadata]',
            'List[Metadata]': 'List[Union[files.FileMetadata, files.FolderMetadata, files.DeletedMetadata]]',
            'List[str]': 'List[str]',
            'List[int]': 'List[int]',
            'List[bool]': 'List[bool]',
            
            # Optional types
            'Optional[Any]': 'Optional[Union[str, int, bool]]',
            'Optional[str]': 'Optional[str]',
            'Optional[int]': 'Optional[int]',
            'Optional[bool]': 'Optional[bool]',
            'Optional[float]': 'Optional[float]',
            
            # Common parameter types
            'datetime': 'datetime.datetime',
            'bytes': 'bytes',
            'BinaryIO': 'BinaryIO',
            
            # Response wrapper
            'DropboxResponse': 'DropboxResponse'
        }
        
    def _get_sdk_modules(self) -> Dict[str, object]:
        """Get all Dropbox SDK modules to introspect."""
        modules = {
            'client': dropbox.Dropbox,
            'team_client': dropbox.DropboxTeam,
        }
        
        # Try to import additional modules if available
        try:
            from dropbox import files, users, sharing, team, paper, file_requests, auth, check # type: ignore
            modules.update({
                'files': files,
                'users': users,
                'sharing': sharing,
                'team': team,
                'paper': paper,
                'file_requests': file_requests,
                'auth': auth,
                'check': check
            })
        except ImportError as e:
            print(f"⚠️  Some dropbox modules not available: {e}")
            
        return modules
    
    def _construct_api_endpoint(self, method_name: str, client_type: str = "user") -> str:
        """Construct API endpoint from SDK method name."""
        # Handle team methods
        if client_type == "team":
            if method_name.startswith("team_"):
                # Remove team_ prefix for endpoint construction
                base_method = method_name[5:]
            else:
                base_method = method_name
            
            # Construct team endpoint
            if "_" in base_method:
                namespace, action = base_method.split("_", 1)
                return f"/2/team/{namespace}/{action}"
            else:
                return f"/2/team/{base_method}"
        
        # Handle user methods
        if "_" in method_name:
            parts = method_name.split("_")
            namespace = parts[0]
            action = "_".join(parts[1:])
            
            # Special cases for Dropbox API versioning
            if namespace in ["files", "users", "sharing", "paper", "file_requests", "auth", "check"]:
                return f"/2/{namespace}/{action}"
            elif namespace == "contacts":
                return f"/2/contacts/{action}"
            elif namespace == "file_properties":
                return f"/2/file_properties/{action}"
            elif namespace == "secondary_emails":
                return f"/2/users/{action}"  # Secondary emails are under users
            elif namespace == "account":
                return f"/2/users/{action}"  # Account methods are under users
            elif namespace == "async":
                return f"/2/async/{action}"
            elif namespace == "cloud_docs":
                return f"/2/cloud_docs/{action}"
            elif namespace == "seen_state":
                return f"/2/seen_state/{action}"
            else:
                return f"/2/{namespace}/{action}"
        else:
            # Single word methods (rare)
            return f"/2/{method_name}"
    
    def _is_api_method(self, method_name: str, method_obj: object) -> bool:
        """Determine if a method is a public API method."""
        # Skip private methods
        if method_name.startswith('_'):
            return False
            
        # Skip common non-API methods
        skip_methods = {
            'clone', 'with_path_root', 'refresh', 'set_oauth2_access_token',
            'set_oauth2_refresh_token', 'check_and_refresh_access_token'
        }
        if method_name in skip_methods:
            return False
            
        # Must be callable
        if not callable(method_obj):
            return False
            
        # Check if it's likely an API method (has certain patterns)
        api_prefixes = [
            'files_', 'users_', 'sharing_', 'team_', 'paper_', 'file_requests_',
            'auth_', 'check_', 'contacts_', 'file_properties_', 'secondary_emails_',
            'account_', 'async_', 'cloud_docs_', 'seen_state_'
        ]
        
        return any(method_name.startswith(prefix) for prefix in api_prefixes)
    
    def _extract_method_info(self, method_name: str, method_obj: object, client_type: str) -> Optional[Dict[str, Union[str, Dict, bool]]]:
        """Extract method information from SDK method."""
        try:
            # Get method signature
            sig = inspect.signature(method_obj)
            
            # Get docstring
            doc = inspect.getdoc(method_obj) or f"Dropbox SDK method: {method_name}"
            
            # Extract parameters
            parameters = {}
            for param_name, param in sig.parameters.items():
                if param_name == 'self':
                    continue
                    
                param_info = {
                    'type': self._get_param_type_string(param),
                    'required': param.default == inspect.Parameter.empty,
                    'description': f"Parameter for {method_name}"
                }
                
                if param.default != inspect.Parameter.empty:
                    param_info['default'] = param.default
                    
                parameters[param_name] = param_info
            
            # Extract return type
            return_annotation = sig.return_annotation
            return_type = self._get_return_type_string(return_annotation)
            
            # Determine namespace from method name
            namespace = self._extract_namespace(method_name)
            
            # Construct API endpoint
            endpoint = self._construct_api_endpoint(method_name, client_type)
            
            return {
                'namespace': namespace,
                'method': method_name.replace(f"{namespace}_", "") if namespace else method_name,
                'sdk_method': method_name,
                'description': doc.split('\n')[0] if doc else f"Dropbox {method_name}",
                'client_type': client_type,
                'parameters': parameters,
                'returns': return_type,
                'full_doc': doc,
                'endpoint': endpoint
            }
            
        except Exception as e:
            print(f"Warning: Could not extract info for {method_name}: {e}")
            return None
    
    def _get_param_type_string(self, param: inspect.Parameter) -> str:
        """Get parameter type as string."""
        if param.annotation != inspect.Parameter.empty:
            return self._format_type_annotation(param.annotation)
        
        # Default to string for unknown parameter types
        return "str"
    
    def _get_return_type_string(self, annotation: object) -> str:
        """Get return type as string."""
        if annotation != inspect.Signature.empty:
            formatted_type = self._format_type_annotation(annotation)
            # Map to specific types if needed
            return self.type_mappings.get(formatted_type, formatted_type)
        
        # Default return type - most Dropbox SDK methods return specific objects
        return "Union[files.FileMetadata, files.FolderMetadata, users.FullAccount, str, bool]"
    
    def _format_type_annotation(self, annotation: object) -> str:
        """Format type annotation as string."""
        if hasattr(annotation, '__name__'):
            base_name = annotation.__name__
            return self.type_mappings.get(base_name, base_name)
        elif hasattr(annotation, '__origin__'):
            # Handle generic types like List[str], Optional[int], etc.
            origin = annotation.__origin__
            args = getattr(annotation, '__args__', ())
            
            if origin is type(None):
                return "None"
            elif origin is list:
                if args:
                    arg_type = self._format_type_annotation(args[0])
                    return f"List[{arg_type}]"
                return "List[str]"  # Default to List[str] instead of List[Any]
            elif origin is dict:
                if len(args) >= 2:
                    key_type = self._format_type_annotation(args[0])
                    value_type = self._format_type_annotation(args[1])
                    return f"Dict[{key_type}, {value_type}]"
                return "Dict[str, str]"  # Default to Dict[str, str] instead of Dict[str, Any]
            elif origin is tuple:
                if args:
                    arg_types = [self._format_type_annotation(arg) for arg in args]
                    return f"Tuple[{', '.join(arg_types)}]"
                return "Tuple[str, ...]"
            elif origin is Union:
                if args:
                    # Handle Optional types (Union[X, None])
                    if len(args) == 2 and type(None) in args:
                        non_none_type = next(arg for arg in args if arg is not type(None))
                        inner_type = self._format_type_annotation(non_none_type)
                        return f"Optional[{inner_type}]"
                    else:
                        arg_types = [self._format_type_annotation(arg) for arg in args]
                        return f"Union[{', '.join(arg_types)}]"
                return "Union[str, int, bool]"  # Default union instead of Any
            elif hasattr(origin, '__name__'):
                return origin.__name__
        
        # Convert string representation and clean up
        str_annotation = str(annotation).replace('typing.', '').replace('NoneType', 'None')
        return self.type_mappings.get(str_annotation, str_annotation)
    
    def _extract_namespace(self, method_name: str) -> str:
        """Extract namespace from method name."""
        parts = method_name.split('_')
        if len(parts) > 1:
            return parts[0]
        return "root"
    
    def _sanitize_param_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        if name and name[0].isdigit():
            name = f"param_{name}"
        python_keywords = {'if', 'for', 'while', 'def', 'class', 'import', 'from', 'as', 'return', 'async', 'await'}
        if name in python_keywords:
            name = f"{name}_param"
        return name
    
    def _discover_sdk_methods(self) -> Dict[str, Dict[str, Union[str, Dict, bool]]]:
        """Discover all API methods from the Dropbox SDK."""
        discovered_methods = {}
        
        # Inspect main Dropbox client
        client_class = self.sdk_modules['client']
        for method_name in dir(client_class):
            method_obj = getattr(client_class, method_name)
            if self._is_api_method(method_name, method_obj):
                method_info = self._extract_method_info(method_name, method_obj, 'user')
                if method_info:
                    discovered_methods[method_name] = method_info
        
        # Inspect team client
        team_client_class = self.sdk_modules['team_client']
        for method_name in dir(team_client_class):
            method_obj = getattr(team_client_class, method_name)
            if self._is_api_method(method_name, method_obj):
                method_info = self._extract_method_info(method_name, method_obj, 'team')
                if method_info:
                    # Prefix team methods to avoid conflicts
                    team_method_name = f"team_{method_name}" if not method_name.startswith('team_') else method_name
                    discovered_methods[team_method_name] = method_info
        
        return discovered_methods
    
    def _generate_method_signature(self, endpoint_id: str, endpoint_info: Dict[str, Union[str, Dict, bool]]) -> Tuple[str, str]:
        """Generate the method signature."""
        method_name = endpoint_id
        parameters = endpoint_info.get("parameters", {})
        
        required_params = []
        optional_params = []
        
        for param_name, param_info in parameters.items():
            clean_name = self._sanitize_param_name(param_name)
            param_type = param_info["type"]
            
            if param_info.get("required", False):
                required_params.append(f"{clean_name}: {param_type}")
            else:
                default_val = param_info.get("default")
                if default_val is not None:
                    if isinstance(default_val, str):
                        default_val = f'"{default_val}"'
                    elif default_val is None:
                        default_val = "None"
                    optional_params.append(f"{clean_name}: {param_type} = {default_val}")
                else:
                    optional_params.append(f"{clean_name}: Optional[{param_type}] = None")
        
        all_params = ['self'] + required_params + optional_params
        returns = "DropboxResponse"
        
        if len(all_params) == 1:
            signature = f"async def {method_name}(self) -> {returns}:"
        else:
            params_formatted = ',\n        '.join(all_params)
            signature = f"async def {method_name}(\n        {params_formatted}\n    ) -> {returns}:"
        
        return signature, method_name
    
    def _generate_docstring(self, endpoint_info: Dict[str, Union[str, Dict, bool]]) -> str:
        """Generate method docstring."""
        description = endpoint_info.get("description", "Dropbox SDK method")
        full_doc = endpoint_info.get("full_doc", "")
        namespace = endpoint_info.get("namespace", "")
        method = endpoint_info.get("method", "")
        client_type = endpoint_info.get("client_type", "user")
        endpoint = endpoint_info.get("endpoint", "Unknown")
        example = endpoint_info.get("example", "")
        response_example = endpoint_info.get("response_example", "")
        
        # Use the first line of the original docstring as description
        if full_doc:
            description = full_doc.split('\n')[0].strip()
        
        docstring = f'        """{description}\n\n'
        docstring += f'        API Endpoint: {endpoint}\n'
        docstring += f'        Namespace: {namespace}\n'
        docstring += f'        Client type: {client_type}\n'
        
        parameters = endpoint_info.get("parameters", {})
        if parameters:
            docstring += '\n        Args:\n'
            for param_name, param_info in parameters.items():
                clean_name = self._sanitize_param_name(param_name)
                param_type = param_info["type"]
                required_text = 'required' if param_info.get("required", False) else 'optional'
                param_desc = param_info.get("description", "")
                docstring += f'            {clean_name} ({param_type}, {required_text}): {param_desc}\n'
        
        returns = "DropboxResponse"
        docstring += f'\n        Returns:\n            {returns}: SDK response\n'
        
        # Add usage example
        if example and str(example).strip():
            docstring += f'\n        Example:\n'
            for line in str(example).strip().split('\n'):
                docstring += f'            {line}\n'
        
        # Add response example
        if response_example and str(response_example).strip():
            docstring += f'\n        Response Example:\n'
            for line in str(response_example).strip().split('\n'):
                docstring += f'            {line}\n'
        
        # Add original docstring if available
        if full_doc and len(full_doc.split('\n')) > 1:
            docstring += f'\n        Original SDK Documentation:\n'
            for line in full_doc.split('\n')[1:]:
                if line.strip():
                    docstring += f'            {line.strip()}\n'
        
        docstring += '        """'
        return docstring
    
    def _generate_method_body(self, endpoint_id: str, endpoint_info: Dict[str, Union[str, Dict, bool]]) -> str:
        """Generate the method body that calls Dropbox SDK."""
        sdk_method = endpoint_info.get("sdk_method", endpoint_id)
        parameters = endpoint_info.get("parameters", {})
        client_type = endpoint_info.get("client_type", "user")
        
        # Determine which client to use
        if client_type == "team":
            client_ref = "self._get_team_client()"
        else:
            client_ref = "self._get_user_client()"
        
        # Build parameter list for SDK call
        if parameters:
            param_assignments = []
            method_params = []
            
            for param_name, param_info in parameters.items():
                clean_name = self._sanitize_param_name(param_name)
                
                if param_info.get("required", False):
                    method_params.append(clean_name)
                else:
                    # Handle optional parameters
                    method_params.append(clean_name)
                    param_assignments.append(
                        f"        # Handle optional parameter: {clean_name}"
                    )
            
            # Filter out None values for optional parameters
            filtered_params = []
            for param_name, param_info in parameters.items():
                clean_name = self._sanitize_param_name(param_name)
                if param_info.get("required", False):
                    filtered_params.append(clean_name)
                else:
                    filtered_params.append(f"{clean_name}={clean_name}")
            
            params_str = ", ".join([p for p in filtered_params if not p.endswith("=None")])
            if params_str:
                method_call = f"client.{sdk_method}({params_str})"
            else:
                method_call = f"client.{sdk_method}()"
        else:
            method_call = f"client.{sdk_method}()"
        
        # Handle team method name adjustments
        actual_sdk_method = str(sdk_method)
        if client_type == "team" and actual_sdk_method.startswith("team_team_"):
            actual_sdk_method = actual_sdk_method.replace("team_team_", "team_")
        elif client_type == "team" and endpoint_id.startswith("team_") and not actual_sdk_method.startswith("team_"):
            actual_sdk_method = actual_sdk_method
        
        method_call = method_call.replace(str(sdk_method), actual_sdk_method)
        
        method_body = f"""        client = {client_ref}
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(None, lambda: {method_call})
            return DropboxResponse(success=True, data=response)
        except Exception as e:
            return DropboxResponse(success=False, error=str(e))"""
        
        return method_body
    
    def generate_dropbox_client(self) -> str:
        """Generate the complete Dropbox client class."""
        
        # Discover all SDK methods
        print("🔍 Discovering Dropbox SDK methods...")
        discovered_methods = self._discover_sdk_methods()
        print(f"📊 Found {len(discovered_methods)} SDK methods")
        
        class_name = "DropboxDataSource"
        description = "Complete Dropbox API client wrapper using official SDK"
        
        class_code = f'''from typing import Dict, List, Optional, Tuple, Union, BinaryIO
from typing import Dict, List, Optional, Union
import asyncio
from dropbox import Dropbox, DropboxTeam
from dropbox.files import ( # type: ignore
    ListRevisionsMode,
    SearchMode,
    TemplateFilter,
    ThumbnailFormat,
    ThumbnailMode,
    ThumbnailSize,
    UpdateFileRequestDeadline,
    WriteMode,
)
from dropbox.paper import ( # type: ignore
    ListPaperDocsFilterBy,
    ListPaperDocsSortBy,
    ListPaperDocsSortOrder,
    UserOnPaperDocFilter,
)
from dropbox.team import AccessInheritance, AccessLevel # type: ignore

from app.sources.client.dropbox.dropbox_ import DropboxClient, DropboxResponse

class {class_name}:
    """
    {description}
    Auto-generated wrapper for Dropbox SDK methods.
    
    This class provides unified access to all Dropbox SDK methods while
    maintaining the official SDK structure and behavior.
    
    Coverage:
    - Total SDK methods: {len(discovered_methods)}
    - Auto-discovered from official Dropbox Python SDK
    """
    
    def __init__(self, dropboxClient: DropboxClient) -> None:
        """
        Initialize the Dropbox SDK wrapper.
        
        Args:
            dropboxClient (DropboxClient): Dropbox client instance
        """
        self._dropbox_client = dropboxClient
        self._user_client = None
        self._team_client = None

    def _get_user_client(self) -> Dropbox:
        """Get or create user client."""
        if self._user_client is None:
            self._user_client = self._dropbox_client.get_client()
        return self._user_client
    
    def _get_team_client(self) -> DropboxTeam:
        """Get or create team client."""
        if self._team_client is None:
            self._team_client = self._dropbox_client.get_team_client()
            if self._team_client is None:
                raise Exception("Team operations require team admin token")
        return self._team_client

'''
        
        # Generate all discovered methods
        for endpoint_id, endpoint_info in discovered_methods.items():
            try:
                signature, method_name = self._generate_method_signature(endpoint_id, endpoint_info)
                docstring = self._generate_docstring(endpoint_info)
                method_body = self._generate_method_body(endpoint_id, endpoint_info)
                
                complete_method = f"    {signature}\n{docstring}\n{method_body}\n\n"
                class_code += complete_method
                
                self.generated_methods.append({
                    'name': method_name,
                    'namespace': endpoint_info.get('namespace'),
                    'method': endpoint_info.get('method'),
                    'client_type': endpoint_info.get('client_type'),
                    'params': len(endpoint_info.get('parameters', {})),
                    'endpoint': endpoint_info.get('endpoint')
                })
            except Exception as e:
                print(f"Warning: Failed to generate method {endpoint_id}: {e}")
        
        # Add utility methods
        class_code += '''    def get_user_client(self) -> Dropbox:
        """Get the underlying Dropbox user client."""
        return self._get_user_client()
    
    def get_team_client(self) -> Optional[DropboxTeam]:
        """Get the underlying Dropbox team client (if available)."""
        try:
            return self._get_team_client()
        except Exception:
            return None
    
    def has_team_access(self) -> bool:
        """Check if this client has team access."""
        return self.get_team_client() is not None
    
    def get_sdk_info(self) -> Dict[str, Union[int, bool, List[str], Dict[str, int]]]:
        """Get information about the wrapped SDK methods."""
        user_methods = [m for m in self.generated_methods if m.get('client_type') == 'user']
        team_methods = [m for m in self.generated_methods if m.get('client_type') == 'team']
        
        namespaces: Dict[str, int] = {}
        for method in self.generated_methods:
            namespace = method.get('namespace') or 'root'
            if namespace not in namespaces:
                namespaces[namespace] = 0
            namespaces[namespace] += 1
        
        endpoints = [m.get('endpoint') for m in self.generated_methods if m.get('endpoint')]
        
        return {
            "total_methods": len(self.generated_methods),
            "user_methods": len(user_methods),
            "team_methods": len(team_methods),
            "namespaces": namespaces,
            "has_team_access": self.has_team_access(),
            "endpoints": [e for e in endpoints if e is not None]
        }
'''
        
        return class_code
    
    def save_to_file(self, filename: Optional[str] = None):
        """Generate and save the complete class to a file."""
        if filename is None:
            filename = "dropbox_sdk_client.py"
            
        # Create dropbox directory in the same folder as the running script
        script_dir = Path(__file__).parent if __file__ else Path('.')
        dropbox_dir = script_dir / 'dropbox'
        dropbox_dir.mkdir(exist_ok=True)
        
        # Set the full file path
        full_path = dropbox_dir / filename
        
        class_code = self.generate_dropbox_client()
        
        full_path.write_text(class_code, encoding='utf-8')
        
        print(f"✅ Generated Dropbox SDK wrapper with {len(self.generated_methods)} methods")
        print(f"📁 Saved to: {full_path}")
        
        # Print summary
        user_count = len([m for m in self.generated_methods if m['client_type'] == 'user'])
        team_count = len([m for m in self.generated_methods if m['client_type'] == 'team'])
        
        print(f"\n📊 Summary:")
        print(f"   - Total methods: {len(self.generated_methods)}")
        print(f"   - User SDK methods: {user_count}")
        print(f"   - Team SDK methods: {team_count}")
        
        namespaces: Dict[str, int] = {}
        endpoints = set()
        for method in self.generated_methods:
            namespace = method['namespace'] or 'root'
            if namespace not in namespaces:
                namespaces[namespace] = 0
            namespaces[namespace] += 1
            
            if method.get('endpoint'):
                endpoints.add(str(method['endpoint']))
        
        print(f"   - Namespaces discovered: {len(namespaces)}")
        for namespace, count in sorted(namespaces.items()):
            print(f"     * {namespace}: {count} methods")
        
        print(f"   - Unique API endpoints: {len(endpoints)}")


def process_dropbox_sdk_api(filename: Optional[str] = None) -> None:
    """End-to-end pipeline for Dropbox SDK API generation."""
    print(f"🚀 Starting Dropbox SDK method discovery and generation...")
    
    generator = DropboxSDKMethodGenerator()
    
    try:
        print("⚙️  Introspecting Dropbox SDK and generating wrapper methods...")
        generator.save_to_file(filename)
        
        script_dir = Path(__file__).parent if __file__ else Path('.')
        print(f"\n📂 Files generated in: {script_dir / 'dropbox'}")
        
        print(f"\n🎉 Successfully generated wrapper for all discoverable Dropbox SDK methods!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise


def main():
    """Main function for Dropbox SDK generator."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate Dropbox API client by introspecting the official SDK')
    parser.add_argument('--filename', '-f', help='Output filename (optional)')
    
    args = parser.parse_args()
    
    try:
        process_dropbox_sdk_api(args.filename)
        return 0
    except Exception as e:
        print(f"❌ Failed to generate SDK wrapper: {e}")
        return 1


if __name__ == "__main__":
    exit(main())