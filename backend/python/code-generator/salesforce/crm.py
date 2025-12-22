# ruff: noqa
"""
Salesforce API Code Generator

Generates a comprehensive SalesforceDataSource class based on the Salesforce REST API.
This generator covers:
- Core SObject resources (CRUD, Describe, Global Describe)
- Query & Search resources (SOQL, SOSL)
- Composite resources (Tree, Batch, SObject Collections)
- Bulk API v2 (Jobs, Batches)
- UI API (List Views, Favorites, Record UI)
- Limits & Analytics

The generated class uses explicit type hints and avoids **kwargs for core parameters.
"""

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ============================================================================
# SALESFORCE API ENDPOINT DEFINITIONS
# ============================================================================

SALESFORCE_ENDPOINTS = {
    # ========================================================================
    # QUERY & SEARCH
    # ========================================================================
    'query': {
        'method': 'GET',
        'path': '/query',
        'description': 'Execute a SOQL query',
        'parameters': {
            'q': {'type': 'str', 'location': 'query', 'required': True, 'description': 'The SOQL query string'}
        }
    },
    'query_all': {
        'method': 'GET',
        'path': '/queryAll',
        'description': 'Execute a SOQL query (includes deleted/archived records)',
        'parameters': {
            'q': {'type': 'str', 'location': 'query', 'required': True, 'description': 'The SOQL query string'}
        }
    },
    'search': {
        'method': 'GET',
        'path': '/search',
        'description': 'Execute a SOSL search',
        'parameters': {
            'q': {'type': 'str', 'location': 'query', 'required': True, 'description': 'The SOSL search string'}
        }
    },

    # ========================================================================
    # SOBJECT BASIC INFORMATION & METADATA
    # ========================================================================
    'describe_global': {
        'method': 'GET',
        'path': '/sobjects',
        'description': 'Lists available objects and their metadata',
        'parameters': {}
    },
    'describe_sobject': {
        'method': 'GET',
        'path': '/sobjects/{sobject}/describe',
        'description': 'Completely describes the individual metadata at all levels for the specified object',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name (e.g., Account)'}
        }
    },
    'get_sobject_info': {
        'method': 'GET',
        'path': '/sobjects/{sobject}',
        'description': 'Retrieves basic metadata for a specific object',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'}
        }
    },

    # ========================================================================
    # SOBJECT ROWS (CRUD)
    # ========================================================================
    'create_record': {
        'method': 'POST',
        'path': '/sobjects/{sobject}',
        'description': 'Create a new record',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'data': {'type': 'Dict[str, Any]', 'location': 'body', 'required': True, 'description': 'JSON data for the record'}
        }
    },
    'get_record': {
        'method': 'GET',
        'path': '/sobjects/{sobject}/{record_id}',
        'description': 'Retrieve a record by ID',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'record_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Record ID'},
            'fields': {'type': 'List[str]', 'location': 'query', 'required': False, 'description': 'List of fields to return'}
        }
    },
    'update_record': {
        'method': 'PATCH',
        'path': '/sobjects/{sobject}/{record_id}',
        'description': 'Update a record by ID',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'record_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Record ID'},
            'data': {'type': 'Dict[str, Any]', 'location': 'body', 'required': True, 'description': 'Fields to update'}
        }
    },
    'delete_record': {
        'method': 'DELETE',
        'path': '/sobjects/{sobject}/{record_id}',
        'description': 'Delete a record by ID',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'record_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Record ID'}
        }
    },
    'upsert_record': {
        'method': 'PATCH',
        'path': '/sobjects/{sobject}/{external_id_field}/{external_id}',
        'description': 'Upsert a record using an external ID',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'external_id_field': {'type': 'str', 'location': 'path', 'required': True, 'description': 'External ID field name'},
            'external_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'External ID value'},
            'data': {'type': 'Dict[str, Any]', 'location': 'body', 'required': True, 'description': 'Record data'}
        }
    },
    'get_record_blob': {
        'method': 'GET',
        'path': '/sobjects/{sobject}/{record_id}/{blob_field}',
        'description': 'Retrieves the specified blob field (e.g., Body on Attachment) for a record',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'record_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Record ID'},
            'blob_field': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Name of the blob field'}
        }
    },

    # ========================================================================
    # SOBJECT COLLECTIONS (BULKISH CRUD)
    # ========================================================================
    'get_records_collection': {
        'method': 'GET',
        'path': '/composite/sobjects/{sobject}',
        'description': 'Retrieve multiple records by ID',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'ids': {'type': 'List[str]', 'location': 'query', 'required': True, 'description': 'List of record IDs'},
            'fields': {'type': 'List[str]', 'location': 'query', 'required': False, 'description': 'List of fields to return'}
        }
    },
    'create_records_collection': {
        'method': 'POST',
        'path': '/composite/sobjects',
        'description': 'Create up to 200 records',
        'parameters': {
            'records': {'type': 'List[Dict[str, Any]]', 'location': 'body', 'required': True, 'description': 'List of SObject records to create (must include attributes.type)'},
            'all_or_none': {'type': 'bool', 'location': 'body', 'required': False, 'description': 'Rollback on any error'}
        }
    },
    'update_records_collection': {
        'method': 'PATCH',
        'path': '/composite/sobjects',
        'description': 'Update up to 200 records',
        'parameters': {
            'records': {'type': 'List[Dict[str, Any]]', 'location': 'body', 'required': True, 'description': 'List of SObject records to update (must include id)'},
            'all_or_none': {'type': 'bool', 'location': 'body', 'required': False, 'description': 'Rollback on any error'}
        }
    },
    'delete_records_collection': {
        'method': 'DELETE',
        'path': '/composite/sobjects',
        'description': 'Delete up to 200 records',
        'parameters': {
            'ids': {'type': 'List[str]', 'location': 'query', 'required': True, 'description': 'List of record IDs to delete'},
            'all_or_none': {'type': 'bool', 'location': 'query', 'required': False, 'description': 'Rollback on any error'}
        }
    },

    # ========================================================================
    # DATA REPLICATION (UPDATED/DELETED)
    # ========================================================================
    'get_updated_records': {
        'method': 'GET',
        'path': '/sobjects/{sobject}/updated',
        'description': 'Get IDs of records updated in a time range',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'start': {'type': 'str', 'location': 'query', 'required': True, 'description': 'Start time (ISO 8601)'},
            'end': {'type': 'str', 'location': 'query', 'required': True, 'description': 'End time (ISO 8601)'}
        }
    },
    'get_deleted_records': {
        'method': 'GET',
        'path': '/sobjects/{sobject}/deleted',
        'description': 'Get IDs of records deleted in a time range',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'},
            'start': {'type': 'str', 'location': 'query', 'required': True, 'description': 'Start time (ISO 8601)'},
            'end': {'type': 'str', 'location': 'query', 'required': True, 'description': 'End time (ISO 8601)'}
        }
    },

    # ========================================================================
    # COMPOSITE API (TREE & BATCH)
    # ========================================================================
    'composite_batch': {
        'method': 'POST',
        'path': '/composite/batch',
        'description': 'Execute up to 25 subrequests in a single batch',
        'parameters': {
            'batch_requests': {'type': 'List[Dict[str, Any]]', 'location': 'body', 'required': True, 'description': 'List of subrequests'},
            'halt_on_error': {'type': 'bool', 'location': 'body', 'required': False, 'description': 'Stop processing on error'}
        }
    },
    'create_sobject_tree': {
        'method': 'POST',
        'path': '/composite/tree/{sobject}',
        'description': 'Create a tree of SObjects (up to 200 records)',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Root object name'},
            'records': {'type': 'List[Dict[str, Any]]', 'location': 'body', 'required': True, 'description': 'Record tree data'}
        }
    },

    # ========================================================================
    # BULK API V2
    # ========================================================================
    'create_job': {
        'method': 'POST',
        'path': '/jobs/ingest',
        'description': 'Create a new Bulk v2 ingest job',
        'parameters': {
            'object': {'type': 'str', 'location': 'body', 'required': True, 'description': 'Object type'},
            'operation': {'type': 'str', 'location': 'body', 'required': True, 'description': 'Operation (insert, delete, update, upsert)'},
            'contentType': {'type': 'str', 'location': 'body', 'required': False, 'description': 'CSV (default)'}
        }
    },
    'get_job_info': {
        'method': 'GET',
        'path': '/jobs/ingest/{job_id}',
        'description': 'Get details about a bulk job',
        'parameters': {
            'job_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Job ID'}
        }
    },
    'close_job': {
        'method': 'PATCH',
        'path': '/jobs/ingest/{job_id}',
        'description': 'Close a bulk job (marks it as UploadComplete)',
        'parameters': {
            'job_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Job ID'},
            'state': {'type': 'str', 'location': 'body', 'required': True, 'description': 'Set to "UploadComplete"'}
        }
    },
    'put_job_data': {
        'method': 'PUT',
        'path': '/jobs/ingest/{job_id}/batches',
        'description': 'Upload CSV data for a bulk job',
        'parameters': {
            'job_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Job ID'},
            'content': {'type': 'str', 'location': 'body', 'required': True, 'description': 'CSV Content (String)'}
        }
    },

    # ========================================================================
    # UI API (USEFUL FOR BUILDING CRM INTERFACES)
    # ========================================================================
    'get_list_views': {
        'method': 'GET',
        'path': '/ui-api/object-info/{sobject}/list-views',
        'description': 'Get list views for an object',
        'parameters': {
            'sobject': {'type': 'str', 'location': 'path', 'required': True, 'description': 'Object name'}
        }
    },
    'get_list_view_records': {
        'method': 'GET',
        'path': '/ui-api/list-ui/{list_view_id}',
        'description': 'Get records and metadata for a specific list view',
        'parameters': {
            'list_view_id': {'type': 'str', 'location': 'path', 'required': True, 'description': 'List View ID'},
            'pageSize': {'type': 'int', 'location': 'query', 'required': False, 'description': 'Number of records per page'}
        }
    },
    'get_record_ui': {
        'method': 'GET',
        'path': '/ui-api/record-ui/{record_ids}',
        'description': 'Get layout information and data for specific records',
        'parameters': {
            'record_ids': {'type': 'List[str]', 'location': 'path', 'required': True, 'description': 'Comma separated record IDs'},
            'layoutTypes': {'type': 'str', 'location': 'query', 'required': False, 'description': 'Full or Compact'}
        }
    },
    'get_favorites': {
        'method': 'GET',
        'path': '/ui-api/favorites',
        'description': 'Get user favorites',
        'parameters': {}
    },

    # ========================================================================
    # SYSTEM & LIMITS
    # ========================================================================
    'get_limits': {
        'method': 'GET',
        'path': '/limits',
        'description': 'Lists information about organization limits',
        'parameters': {}
    },
    'recent_items': {
        'method': 'GET',
        'path': '/recent',
        'description': 'Get recently viewed items',
        'parameters': {
            'limit': {'type': 'int', 'location': 'query', 'required': False, 'description': 'Max items to return'}
        }
    }
}


def generate_method_signature(method_name: str, config: Dict[str, Any]) -> str:
    """Generate the async method definition."""
    params = ["self"]
    
    # Sort: required first, then optional
    sorted_params = sorted(
        config['parameters'].items(), 
        key=lambda x: (not x[1].get('required', False), x[0])
    )

    for param_name, param_cfg in sorted_params:
        p_type = param_cfg['type']
        if param_cfg.get('required', False):
            params.append(f"{param_name}: {p_type}")
        else:
            params.append(f"{param_name}: Optional[{p_type}] = None")

    return f"    async def {method_name}({', '.join(params)}) -> SalesforceResponse:"


def generate_docstring(config: Dict[str, Any]) -> str:
    """Generate method docstring."""
    lines = [f'        """{config["description"]}']
    
    if config['parameters']:
        lines.append('')
        lines.append('        Args:')
        sorted_params = sorted(
            config['parameters'].items(), 
            key=lambda x: (not x[1].get('required', False), x[0])
        )
        for param_name, param_cfg in sorted_params:
            desc = param_cfg.get('description', '')
            lines.append(f"            {param_name}: {desc}")
            
    lines.append('')
    lines.append('        Returns:')
    lines.append('            SalesforceResponse: API response object')
    lines.append('        """')
    return "\n".join(lines)


def generate_method_body(config: Dict[str, Any]) -> str:
    """Generate the implementation body."""
    lines = []
    
    # 1. Prepare Path parameters
    path_params = {k: v for k, v in config['parameters'].items() if v['location'] == 'path'}
    url_str = f'"{config["path"]}"'
    
    if path_params:
        # Handle list in path (e.g. comma separated ids)
        for p_name, p_cfg in path_params.items():
            if 'List' in p_cfg['type']:
                lines.append(f"        if isinstance({p_name}, list):")
                lines.append(f"            {p_name} = ','.join({p_name})")
        
        format_args = ", ".join(f"{k}={k}" for k in path_params.keys())
        lines.append(f"        path = {url_str}.format({format_args})")
    else:
        lines.append(f"        path = {url_str}")

    # 2. Prepare Query parameters
    query_params = {k: v for k, v in config['parameters'].items() if v['location'] == 'query'}
    if query_params:
        lines.append("        params = {}")
        for p_name, p_cfg in query_params.items():
            lines.append(f"        if {p_name} is not None:")
            if 'List' in p_cfg['type']:
                lines.append(f"            params['{p_name}'] = ','.join({p_name})")
            else:
                lines.append(f"            params['{p_name}'] = {p_name}")
    
    # 3. Prepare Body
    body_params = {k: v for k, v in config['parameters'].items() if v['location'] == 'body'}
    
    # Special case: if one body param is named 'data' or 'records' or 'content' and it's the only one/main one
    # OR if we have multiple body params that need to be merged into a dict
    
    has_body = False
    
    if len(body_params) == 1:
        # Single body payload (e.g. create_record(data=...))
        p_name = list(body_params.keys())[0]
        # For put_job_data, content is a string (CSV), passing it directly as body
        if p_name == 'content':
             lines.append(f"        # Body is raw string content")
        elif p_name == 'data' or p_name == 'records' or p_name == 'batch_requests':
             # Usually passed directly as the JSON body
             pass
        else:
             # Construct dict
             lines.append("        body_payload = {}")
             lines.append(f"        if {p_name} is not None:")
             lines.append(f"            body_payload['{p_name}'] = {p_name}")

        has_body = True
    elif len(body_params) > 1:
        lines.append("        body_payload = {}")
        for p_name in body_params.keys():
             lines.append(f"        if {p_name} is not None:")
             lines.append(f"            body_payload['{p_name}'] = {p_name}")
        has_body = True

    # 4. Request Construction
    lines.append("")
    lines.append("        # Construct full URL")
    lines.append("        url = self.base_url + path")
    lines.append("")
    lines.append("        headers = self.client.headers.copy()")
    
    # Handle CSV content type for Bulk API
    if 'put_job_data' in config['path']:
         lines.append("        headers['Content-Type'] = 'text/csv'")

    lines.append("        ")
    lines.append("        request = HTTPRequest(")
    lines.append(f"            method='{config['method']}',")
    lines.append("            url=url,")
    lines.append("            headers=headers,")
    if query_params:
        lines.append("            query_params=params,")
    
    if has_body:
        if len(body_params) == 1:
            p_name = list(body_params.keys())[0]
            if p_name == 'content': # Raw string body (CSV)
                 lines.append(f"            body={p_name}")
            elif p_name in ['data', 'records', 'batch_requests']: # Direct list/dict
                 lines.append(f"            body={p_name}")
            else:
                 lines.append("            body=body_payload")
        else:
            lines.append("            body=body_payload")

    lines.append("        )")
    
    # 5. Execution
    lines.append("")
    lines.append("        try:")
    lines.append("            response = await self.client.execute(request)")
    lines.append("            # Salesforce API usually returns 200/201/204")
    lines.append("            # 204 No Content means success (e.g. DELETE)")
    lines.append("            data = response.json() if response.status != 204 and response.text() else {}")
    lines.append("            return SalesforceResponse(success=response.status < 300, data=data)")
    lines.append("        except Exception as e:")
    lines.append("            return SalesforceResponse(success=False, error=str(e))")

    return "\n".join(lines)


def generate_class() -> str:
    """Generate the full Python class."""
    lines = [
        'from typing import Any, Dict, List, Optional, Union',
        'from app.sources.client.http.http_request import HTTPRequest',
        'from app.sources.client.salesforce.salesforce import SalesforceClient, SalesforceResponse',
        '',
        '',
        'class SalesforceDataSource:',
        '    """Comprehensive Salesforce API Data Source.',
        '    ',
        '    Covers:',
        '    - Core CRM SObjects (Account, Contact, Lead, Opportunity, Custom objects)',
        '    - Query & Search (SOQL, SOSL)',
        '    - Composite API (Tree, Batch, Collections) for efficient bulk operations',
        '    - Bulk API v2 for large data sets',
        '    - UI API for layout and list view metadata',
        '    - System Limits & Info',
        '    """',
        '',
        '    def __init__(self, client: SalesforceClient):',
        '        self.client = client.get_client()',
        '        self.base_url = client.get_base_url()',
        ''
    ]

    for method_name, config in SALESFORCE_ENDPOINTS.items():
        lines.append(generate_method_signature(method_name, config))
        lines.append(generate_docstring(config))
        lines.append(generate_method_body(config))
        lines.append('')

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate Salesforce API Client")
    parser.add_argument("--out", default="salesforce_data_source.py", help="Output file")
    args = parser.parse_args()

    print(f"🚀 Generating Salesforce DataSource...")
    code = generate_class()
    
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(code)
    
    print(f"✅ Generated {out_path} with {len(SALESFORCE_ENDPOINTS)} API methods.")
    print("   - Includes Core CRUD, SOQL/SOSL, Composite, Bulk v2, UI API")

if __name__ == "__main__":
    main()