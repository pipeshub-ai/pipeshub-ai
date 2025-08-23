# ruff: noqa
#!/usr/bin/env python3
"""
Google API Method Generator
Generates all methods from Google Discovery APIs and creates wrapper methods
that use the Google SDK client internally. Supports multiple Google services.
"""

import json
import requests
from typing import Dict, Any, List, Optional, Union
import re
import os
from pathlib import Path


class GoogleAPIMethodGenerator:
    """Generate Google API methods from Discovery document for any Google service."""
    
    def __init__(self, service_name: str, version: str):
        """
        Initialize the generator for a specific Google service.
        
        Args:
            service_name: Google API service name (e.g., 'drive', 'gmail', 'calendar', 'sheets')
            version: API version (e.g., 'v3', 'v1', 'v4')
        """
        self.service_name = service_name.lower()
        self.version = version.lower()
        self.discovery_doc = None
        self.generated_methods = []
        
        # Service configuration mapping
        self.service_configs = {
            'drive': {
                'class_name': 'GoogleDriveDataSource',
                'description': 'Google Drive API',
                'scope_example': 'https://www.googleapis.com/auth/drive'
            },
            'gmail': {
                'class_name': 'GoogleGmailDataSource', 
                'description': 'Gmail API',
                'scope_example': 'https://www.googleapis.com/auth/gmail.readonly'
            },
            'calendar': {
                'class_name': 'GoogleCalendarDataSource',
                'description': 'Google Calendar API', 
                'scope_example': 'https://www.googleapis.com/auth/calendar'
            },
            'sheets': {
                'class_name': 'GoogleSheetsDataSource',
                'description': 'Google Sheets API',
                'scope_example': 'https://www.googleapis.com/auth/spreadsheets'
            },
            'docs': {
                'class_name': 'GoogleDocsDataSource',
                'description': 'Google Docs API',
                'scope_example': 'https://www.googleapis.com/auth/documents'
            },
            'youtube': {
                'class_name': 'YouTubeDataSource',
                'description': 'YouTube Data API',
                'scope_example': 'https://www.googleapis.com/auth/youtube'
            },
            'admin': {
                'class_name': 'GoogleAdminDataSource',
                'description': 'Google Admin SDK Directory API',
                'scope_example': 'https://www.googleapis.com/auth/admin.directory.user.readonly'
            },
            'forms': {
                'class_name': 'GoogleFormsDataSource',
                'description': 'Google Forms API',
                'scope_example': 'https://www.googleapis.com/auth/forms.body.readonly'
            },
            'slides': {
                'class_name': 'GoogleSlidesDataSource',
                'description': 'Google Slides API',
                'scope_example': 'https://www.googleapis.com/auth/presentations'
            },
            'meet': {
                'class_name': 'GoogleMeetDataSource',
                'description': 'Google Meet API',
                'scope_example': 'https://www.googleapis.com/auth/meetings'
             }
        }
        
    def get_service_config(self) -> Dict[str, str]:
        """Get configuration for the current service."""
        return self.service_configs.get(self.service_name, {
            'class_name': 'GoogleClient',
            'description': f'Google {self.service_name.capitalize()} API',
            'scope_example': f'https://www.googleapis.com/auth/{self.service_name}'
        })
        
    def fetch_discovery_document(self) -> Dict[str, Any]:
        """Fetch Google API discovery document (with per-service fallbacks)."""
        # Default discovery directory URL
        primary_url = f"https://www.googleapis.com/discovery/v1/apis/{self.service_name}/{self.version}/rest"
        # Some Google APIs expose discovery only via the $discovery endpoint
        fallback_urls = {
            "forms": f"https://forms.googleapis.com/$discovery/rest?version={self.version}",
            "docs": f"https://docs.googleapis.com/$discovery/rest?version={self.version}",
            "meet": f"https://meet.googleapis.com/$discovery/rest?version={self.version}",
        }
        # Try primary first
        try:
            resp = requests.get(primary_url)
            resp.raise_for_status()
            self.discovery_doc = resp.json()
            return self.discovery_doc
        except requests.RequestException as primary_err:
            # Try known fallback (if any)
            fb_url = fallback_urls.get(self.service_name)
            if not fb_url:
                raise Exception(
                    f"Failed to fetch discovery document for {self.service_name} {self.version}: {primary_err}"
                )
            try:
                resp = requests.get(fb_url)
                resp.raise_for_status()
                self.discovery_doc = resp.json()
                return self.discovery_doc
            except requests.RequestException as fb_err:
                raise Exception(
                    f"Failed to fetch discovery document for {self.service_name} {self.version}: "
                    f"{primary_err} | fallback: {fb_err}"
                )
    
    def _to_snake_case(self, name: str) -> str:
        """Convert camelCase/PascalCase to snake_case."""
        # Insert underscores before capital letters
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        s2 = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1)
        return s2.lower()
    
    def _python_type_mapping(self, param_info: Dict[str, Any]) -> str:
        """Map JSON schema types to Python type hints."""
        schema_type = param_info.get('type', 'string')
        format_type = param_info.get('format')
        
        type_mapping = {
            'string': 'str',
            'integer': 'int', 
            'number': 'float',
            'boolean': 'bool',
            'array': 'List[str]',  # Most Google API arrays are string arrays
            'object': 'Dict[str, Any]'
        }
        
        base_type = type_mapping.get(schema_type, 'Any')
        
        # Handle special formats
        if format_type == 'int64':
            base_type = 'int'
        elif format_type == 'date-time':
            base_type = 'str'  # ISO datetime string
            
        return base_type
    
    def _sanitize_param_name(self, name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers."""
        # Replace invalid characters
        name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
        # Ensure it doesn't start with a number
        if name[0].isdigit():
            name = f"param_{name}"
        # Handle Python keywords
        python_keywords = {'if', 'for', 'while', 'def', 'class', 'import', 'from', 'as', 'return'}
        if name in python_keywords:
            name = f"{name}_param"
        return name
    
    def _extract_parameters(self, method_info: Dict[str, Any]) -> tuple:
        """Extract and process parameters from method info."""
        parameters = method_info.get('parameters', {})
        required_params = []
        optional_params = []
        param_docs = []
        
        # Separate required and optional parameters
        for param_name, param_info in parameters.items():
            clean_name = self._sanitize_param_name(param_name)
            param_type = self._python_type_mapping(param_info)
            description = param_info.get('description', '').replace('\n', ' ')
            is_required = param_info.get('required', False)
            
            if is_required:
                required_params.append(f"{clean_name}: {param_type}")
            else:
                optional_params.append(f"{clean_name}: Optional[{param_type}] = None")
                
            param_docs.append({
                'name': clean_name,
                'original_name': param_name,
                'type': param_type,
                'required': is_required,
                'description': description
            })
        
        return required_params, optional_params, param_docs
    
    def _generate_method_signature(self, resource_name: str, method_name: str, 
                                 required_params: List[str], optional_params: List[str]) -> str:
        """Generate the method signature with snake_case and each parameter on new line."""
        # Convert both resource_name and method_name to snake_case
        resource_snake = self._to_snake_case(resource_name) if resource_name else ""
        method_snake = self._to_snake_case(method_name)
        
        # Sanitize method name for Python keywords
        method_sanitized = self._sanitize_method_name(method_snake)
        method_full_name = f"{resource_snake}_{method_snake}" if resource_snake else method_snake
        
        # Format parameters with each on a new line
        all_params = ['self'] + required_params + optional_params
        if len(all_params) == 1:
            signature = f"def {method_full_name}(self) -> Dict[str, Any]:"
        else:
            params_formatted = ',\n        '.join(all_params)
            signature = f"def {method_full_name}(\n        {params_formatted}\n    ) -> Dict[str, Any]:"
        
        return signature, method_full_name
    
    def _generate_docstring(self, method_info: Dict[str, Any], param_docs: List[Dict]) -> str:
        """Generate method docstring."""
        service_config = self.get_service_config()
        description = method_info.get('description', f'{service_config["description"]} method')
        http_method = method_info.get('httpMethod', 'GET')
        path = method_info.get('path', '')
        
        docstring = f'        """{service_config["description"]}: {description}\n\n'
        docstring += f'        HTTP {http_method} {path}\n'
        
        if param_docs:
            docstring += '\n        Args:\n'
            for param in param_docs:
                required_text = 'required' if param['required'] else 'optional'
                docstring += f'            {param["name"]} ({param["type"]}, {required_text}): {param["description"]}\n'
        
        docstring += '\n        Returns:\n            Dict[str, Any]: API response\n        """'
        return docstring
    
    def _sanitize_method_name(self, method_name: str) -> str:
        """Sanitize method names that conflict with Python keywords."""
        python_keywords = {'import', 'from', 'class', 'def', 'if', 'for', 'while', 'return', 'global', 'async', 'await'}
        if method_name in python_keywords:
            return f"{method_name}_"
        return method_name

    def _generate_method_body(self, resource_name: str, method_name: str, 
                            param_docs: List[Dict], method_info: Dict[str, Any]) -> str:
        """Generate the method body that calls Google SDK."""
        
        # Build the Google SDK client call chain
        original_method_name = method_name
        if method_name in {'import', 'from', 'class', 'def', 'if', 'for', 'while', 'return', 'global', 'async', 'await'}:
            # Build the Google SDK client call chain using getattr for keyword methods
            if resource_name:
                client_call = f"getattr(self.client.{resource_name}(), '{original_method_name}')"
            else:
                client_call = f"getattr(self.client, '{original_method_name}')"
        else:
            # Build the Google SDK client call chain normally
            if resource_name:
                client_call = f"self.client.{resource_name}().{method_name}"
            else:
                client_call = f"self.client.{method_name}"
        
        # Generate parameter mapping
        param_mapping = []
        for param in param_docs:
            clean_name = param['name']
            original_name = param['original_name']
            param_mapping.append(f"        if {clean_name} is not None:\n            kwargs['{original_name}'] = {clean_name}")
        
        # Handle request body for POST/PUT/PATCH methods
        http_method = method_info.get('httpMethod', 'GET')
        body_handling = ""
        if http_method in ['POST', 'PUT', 'PATCH']:
            body_handling = """
        # Handle request body if needed
        if 'body' in kwargs:
            body = kwargs.pop('body')
            request = """ + client_call + """(**kwargs, body=body) # type: ignore
        else:
            request = """ + client_call + """(**kwargs) # type: ignore""" 
        else:
            body_handling = f"\n        request = {client_call}(**kwargs) # type: ignore"
        
        method_body = f"""        kwargs = {{}}
{chr(10).join(param_mapping) if param_mapping else "        # No parameters for this method"}
{body_handling}
        return request.execute()"""
        
        return method_body
    
    def _process_resource_methods(self, resource_name: str, resource_info: Dict[str, Any]) -> List[str]:
        """Process all methods for a given resource."""
        methods = []
        
        if 'methods' not in resource_info:
            return methods
            
        for method_name, method_info in resource_info['methods'].items():
            required_params, optional_params, param_docs = self._extract_parameters(method_info)
            
            # Generate method components
            signature, full_method_name = self._generate_method_signature(
                resource_name, method_name, required_params, optional_params
            )
            docstring = self._generate_docstring(method_info, param_docs)
            method_body = self._generate_method_body(resource_name, method_name, param_docs, method_info)
            
            # Combine into complete method
            complete_method = f"    {signature}\n{docstring}\n{method_body}\n"
            methods.append(complete_method)
            
            self.generated_methods.append({
                'name': full_method_name,
                'resource': resource_name,
                'method': method_name,
                'http_method': method_info.get('httpMethod', 'GET'),
                'path': method_info.get('path', ''),
                'params': len(param_docs)
            })
        
        return methods
    
    def _process_nested_resources(self, resources: Dict[str, Any], parent_path: str = "") -> List[str]:
        """Process nested resources recursively."""
        all_methods = []
        
        for resource_name, resource_info in resources.items():
            current_path = f"{parent_path}_{resource_name}" if parent_path else resource_name
            
            # Process methods for current resource
            methods = self._process_resource_methods(current_path, resource_info)
            all_methods.extend(methods)
            
            # Process nested resources
            if 'resources' in resource_info:
                nested_methods = self._process_nested_resources(resource_info['resources'], current_path)
                all_methods.extend(nested_methods)
        
        return all_methods
    
    def _generate_model_from_schema(self, name: str, schema: Dict[str, Any], indent: str = "") -> str:
        """Generate a Pydantic model from a JSON schema."""
        if not schema:
            return ""
        
        # Handle $ref
        if "$ref" in schema:
            ref_name = schema["$ref"].split("/")[-1]
            return f"{indent}# Reference to {ref_name}\n"
        
        schema_type = schema.get('type', 'object')
        
        if schema_type == 'object':
            properties = schema.get('properties', {})
            required = set(schema.get('required', []))
            
            class_def = f"{indent}class {name}(BaseModel):\n"
            class_def += f"{indent}    \"\"\"{schema.get('description', f'Generated model for {name}')}\"\"\"\n"
            
            if not properties:
                class_def += f"{indent}    pass\n"
            else:
                for prop_name, prop_schema in properties.items():
                    prop_type = self._schema_to_python_type(prop_schema)
                    prop_desc = prop_schema.get('description', '')
                    
                    if prop_name in required:
                        class_def += f"{indent}    {prop_name}: {prop_type}"
                    else:
                        class_def += f"{indent}    {prop_name}: Optional[{prop_type}] = None"
                    
                    if prop_desc:
                        class_def += f"  # {prop_desc}"
                    class_def += "\n"
            
            return class_def + "\n"
        
        return ""

    def _schema_to_python_type(self, schema: Dict[str, Any]) -> str:
        """Convert JSON schema to Python type for models."""
        if not schema:
            return "Any"
        
        if "$ref" in schema:
            return schema["$ref"].split("/")[-1]
        
        schema_type = schema.get('type')
        format_type = schema.get('format', '')
        
        if schema_type == 'string':
            if format_type == 'date-time':
                return "datetime"
            elif format_type == 'date':
                return "date"
            elif format_type == 'byte':
                return "bytes"
            return "str"
        elif schema_type == 'integer':
            return "int"
        elif schema_type == 'number':
            return "float"
        elif schema_type == 'boolean':
            return "bool"
        elif schema_type == 'array':
            items = schema.get('items', {})
            item_type = self._schema_to_python_type(items)
            return f"List[{item_type}]"
        elif schema_type == 'object':
            return "Dict[str, Any]"
        
        return "Any"

    def generate_models(self) -> str:
        """Generate Pydantic models from discovery document schemas."""
        if not self.discovery_doc:
            self.fetch_discovery_document()
        
        schemas = self.discovery_doc.get('schemas', {})
        service_config = self.get_service_config()
        
        models_code = f'''from typing import Dict, Any, Optional, List
from pydantic import BaseModel
from datetime import datetime, date

"""
Auto-generated Pydantic models for {service_config["description"]} {self.version}
Generated from Google Discovery API schema definitions.
"""

'''
        
        # Generate models for each schema
        for schema_name, schema_def in schemas.items():
            model_code = self._generate_model_from_schema(schema_name, schema_def)
            if model_code:
                models_code += model_code
        
        return models_code

    def generate_google_client(self) -> str:
        """Generate the complete GoogleClient class for the specified service."""
        if not self.discovery_doc:
            self.fetch_discovery_document()
        
        service_config = self.get_service_config()
        class_name = service_config['class_name']
        service_description = service_config['description']
        
        # Process all resources and their methods
        resources = self.discovery_doc.get('resources', {})
        all_methods = self._process_nested_resources(resources)
        
        # Generate class header
        class_code = f'''from typing import Dict, Any, Optional, List


class {class_name}:
    """
    Auto-generated {service_description} client wrapper.
    Uses Google SDK client internally for all operations.
    This class wraps all {service_description} {self.version} methods and provides
    a consistent interface while using the official Google SDK.
    """
    
    def __init__(
        self,
        client: object
    ) -> None:
        """
        Initialize with {service_description} client.
        Args:
            client: {service_description} client from build('{self.service_name}', '{self.version}', credentials=credentials)
        """
        self.client = client

'''
        
        # Add all generated methods
        class_code += '\n'.join(all_methods)
        
        # Add utility methods
        class_code += '''
    def get_client(self) -> object:
        """Get the underlying Google API client."""
        return self.client
'''
        
        return class_code
    
    def save_to_file(self, filename: str = None):
        """Generate and save the complete class to a file."""
        if filename is None:
            filename = f"google_{self.service_name}_client.py"
            
        # Create google directory in the same folder as the running script
        script_dir = Path(__file__).parent if __file__ else Path('.')
        google_dir = script_dir / 'google'
        google_dir.mkdir(exist_ok=True)
        
        # Set the full file path
        full_path = google_dir / filename
        
        class_code = self.generate_google_client()
        
        full_path.write_text(class_code, encoding='utf-8')
        
        service_config = self.get_service_config()
        print(f"✅ Generated {service_config['description']} client with {len(self.generated_methods)} methods")
        print(f"📁 Saved to: {full_path}")
        
        # Print summary
        print(f"\n📊 Summary:")
        print(f"   - Service: {self.service_name} {self.version}")
        print(f"   - Total methods: {len(self.generated_methods)}")
        
        resources = {}
        for method in self.generated_methods:
            resource = method['resource'] or 'root'
            if resource not in resources:
                resources[resource] = 0
            resources[resource] += 1
        
        print(f"   - Resources covered: {len(resources)}")
        for resource, count in resources.items():
            print(f"     * {resource}: {count} methods")

    def save_models_to_file(self, filename: str = None):
        """Generate and save Pydantic models to a file."""
        if filename is None:
            filename = f"google_{self.service_name}_models.py"
            
        # Create google directory in the same folder as the running script
        script_dir = Path(__file__).parent if __file__ else Path('.')
        google_dir = script_dir / 'google'
        google_dir.mkdir(exist_ok=True)
        
        # Set the full file path
        full_path = google_dir / filename
        
        models_code = self.generate_models()
        
        full_path.write_text(models_code, encoding='utf-8')
        
        service_config = self.get_service_config()
        schemas_count = len(self.discovery_doc.get('schemas', {}))
        print(f"✅ Generated {service_config['description']} models with {schemas_count} schema definitions")
        print(f"📁 Models saved to: {full_path}")
    
    def generate_usage_example(self) -> str:
        """Generate usage example for the specific service."""
        service_config = self.get_service_config()
        
        # Get some common method examples based on service
        method_examples = self._get_service_method_examples()
        
        return f'''
# Example usage for {service_config["description"]}:
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google.google_{self.service_name}_client import GoogleClient

# Set up credentials and client
credentials = Credentials.from_service_account_file('path/to/service-account.json')
client = build('{self.service_name}', '{self.version}', credentials=credentials)

# Initialize the wrapper
{self.service_name}_client = GoogleClient(client)

# Use the generated methods (in snake_case)
def example():
{method_examples}

# Run the example
example()
'''
    
    def _get_service_method_examples(self) -> str:
        """Get service-specific method examples."""
        examples = {
            'drive': '''    # List files
    files = drive_client.files_list(pageSize=10)
    print("Files:", files)
    
    # Get file metadata
    file_id = "your_file_id"
    file_info = drive_client.files_get(fileId=file_id)
    print("File info:", file_info)
    
    # Get changes
    changes = drive_client.changes_list(
        pageToken='your_token',
        pageSize=100,
        includeRemoved=True
    )
    print("Changes:", changes)''',
        
            'gmail': '''    # List messages
    messages = gmail_client.users_messages_list(userId='me', maxResults=10)
    print("Messages:", messages)
    
    # Get message
    message_id = "your_message_id"
    message = gmail_client.users_messages_get(userId='me', id=message_id)
    print("Message:", message)
    
    # List labels
    labels = gmail_client.users_labels_list(userId='me')
    print("Labels:", labels)''',
        
            'calendar': '''    # List calendars
    calendars = calendar_client.calendar_list_list()
    print("Calendars:", calendars)
    
    # List events
    events = calendar_client.events_list(calendarId='primary', maxResults=10)
    print("Events:", events)
    
    # Get calendar
    calendar = calendar_client.calendars_get(calendarId='primary')
    print("Calendar:", calendar)''',
        
            'sheets': '''    # Get spreadsheet
    spreadsheet_id = "your_spreadsheet_id"
    spreadsheet = sheets_client.spreadsheets_get(spreadsheetId=spreadsheet_id)
    print("Spreadsheet:", spreadsheet)
    
    # Get values
    values = sheets_client.spreadsheets_values_get(
        spreadsheetId=spreadsheet_id,
        range='Sheet1!A1:C10'
    )
    print("Values:", values)''',

            'forms': '''    # List forms
    form_id = "your_form_id"
    form = forms_client.forms_get(formId=form_id)
    print("Form:", form)

    # List responses
    responses = forms_client.forms_responses_list(
        formId=form_id,
        pageSize=10
    )
    print("Responses:", responses)''',
        
            'docs': '''    # Get document
    document_id = "your_document_id"
    document = docs_client.documents_get(documentId=document_id)
    print("Document:", document)

    # Batch update (e.g., insert text)
    body = {
        "requests": [
            {"insertText": {"location": {"index": 1}, "text": "Hello from generator!"}}
        ]
    }
    updated = docs_client.documents_batch_update(documentId=document_id, body=body)
    print("BatchUpdate result:", updated)''',
            'forms': '''    # Get a form
    form_id = "your_form_id"
    form = forms_client.forms_get(formId=form_id)
    print("Form:", form)

    # List responses
    responses = forms_client.forms_responses_list(formId=form_id, pageSize=10)
    print("Responses:", responses)''', 

            'slides': '''    # Get a presentation
    presentation_id = "your_presentation_id"
    presentation = slides_client.presentations_get(presentationId=presentation_id)
    print("Presentation:", presentation)

    # Batch update presentation
    requests = {"requests": []}  # Add requests like createSlide, insertText
    response = slides_client.presentations_batch_update(
        presentationId=presentation_id, body=requests
    )
    print("Batch update response:", response)''',

            'youtube': '''    # List channels
    search = youtube_client.search_list(part='snippet', q='python', maxResults=5)
    print("Search:", search)

    # Get channel details
    channels = youtube_client.channels_list(part='snippet,statistics', forUsername='GoogleDevelopers')
    print("Channels:", channels)

    # Get video details
    videos = youtube_client.videos_list(part='snippet,contentDetails,statistics', id='dQw4w9WgXcQ')
    print("Videos:", videos)''',

            'meet': '''    # List spaces
    spaces = meet_client.spaces_list(pageSize=10)
    print("Spaces:", spaces)

    # Create a space (meeting)
    body = {"space": {"config": {"accessType": "OPEN"}}}
    created = meet_client.spaces_create(body=body)
    print("Created space:", created)''',
        }
        
        return examples.get(self.service_name, f'''    # Example methods for {self.service_name} (check generated methods)
    # Use any of the generated {self.service_name}_ methods
    pass''')


def process_google_service(
    service_name: str,
    version: str,
    filename: Optional[str] = None,
    generate_models: bool = True
) -> None:
    """End-to-end pipeline for a Google service."""
    print(f"🚀 Starting Google {service_name.capitalize()} API {version} method generation...")
    
    generator = GoogleAPIMethodGenerator(service_name, version)
    
    try:
        # Fetch discovery document
        print(f"📡 Fetching Google {service_name.capitalize()} API discovery document...")
        generator.fetch_discovery_document()
        
        # Generate models if requested
        if generate_models:
            print("⚙️  Generating models...")
            generator.save_models_to_file()
        
        # Generate the client class
        print("⚙️  Generating client methods...")
        generator.save_to_file(filename)
        
        # Generate usage example
        print("\n📝 Usage example:")
        print(generator.generate_usage_example())
        
        script_dir = Path(__file__).parent if __file__ else Path('.')
        print(f"\n📂 Files generated in: {script_dir / 'google'}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise


def main():
    """Main function with support for multiple Google services."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate Google API clients from Discovery documents')
    parser.add_argument('service', nargs='?', help='Google API service name (drive, gmail, calendar, sheets, docs, youtube, admin, forms, slides, meet)')
    # Optional alias so you can run: python google.py --api admin
    parser.add_argument('--api', dest='api', help='Alias for service (e.g., admin, drive, gmail, calendar, sheets, docs, youtube, forms, slides, meet)')
    parser.add_argument('--version', '-v', default='v3', help='API version (default: v3)')
    parser.add_argument('--filename', '-f', help='Output filename (optional)')
    parser.add_argument('--no-models', action='store_true', help='Skip model generation')
    
    args = parser.parse_args()
    
    # Prefer --api if provided, else positional service
    selected_service = (args.api or args.service or '').lower()
    # Validate service
    supported_services = ['drive', 'gmail', 'calendar', 'sheets', 'docs', 'youtube', 'admin', 'forms', 'slides', 'meet']
    if selected_service not in supported_services:
        print(f"❌ Unsupported service: {selected_service}")
        print(f"Supported services: {', '.join(supported_services)}")
        return 1
    
    # Default versions for services
    version_defaults = {
        'drive': 'v3',
        'gmail': 'v1', 
        'calendar': 'v3',
        'sheets': 'v4',
        'docs': 'v1',
        'youtube': 'v3',
        'admin': 'directory_v1',
        'forms': 'v1',
        'slides': 'v1',
        'meet': 'v2'
    }
    
    version = args.version
    if (args.version == 'v3') and (selected_service in version_defaults):
        version = version_defaults[selected_service]
        print(f"ℹ️  Using default version {version} for {selected_service}")
    
    try:
        process_google_service(
            selected_service, 
            version, 
            args.filename,
            generate_models=not args.no_models
        )
        return 0
    except Exception as e:
        print(f"❌ Failed to generate client: {e}")
        return 1


# Convenience functions for common services
def generate_drive_client(version: str = 'v3', include_models: bool = True) -> None:
    """Generate Google Drive API client."""
    process_google_service('drive', version, generate_models=include_models)


def generate_gmail_client(version: str = 'v1', include_models: bool = True) -> None:
    """Generate Gmail API client."""
    process_google_service('gmail', version, generate_models=include_models)


def generate_calendar_client(version: str = 'v3', include_models: bool = True) -> None:
    """Generate Google Calendar API client.""" 
    process_google_service('calendar', version, generate_models=include_models)


def generate_sheets_client(version: str = 'v4', include_models: bool = True) -> None:
    """Generate Google Sheets API client."""
    process_google_service('sheets', version, generate_models=include_models)

def generate_admin_client(version: str = 'directory_v1', include_models: bool = True) -> None:
    """Generate Google Admin SDK Directory API client."""
    process_google_service('admin', version, generate_models=include_models)

def generate_forms_client(version: str = 'v1', include_models: bool = True) -> None:
    """Generate Google Forms API client."""
    process_google_service('forms', version, generate_models=include_models)

def generate_slides_client(version: str = 'v1', include_models: bool = True) -> None:
    """Generate Google Slides API client."""
    process_google_service('slides', version, generate_models=include_models)

def generate_meet_client(version: str = 'v2', include_models: bool = True) -> None:
    """Generate Google Meet API client."""
    process_google_service('meet', version, generate_models=include_models)

if __name__ == "__main__":
    exit(main())