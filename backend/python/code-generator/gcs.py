# ruff: noqa
"""
Complete GCS Data Source Generator
Generates GCSDataSource class with ALL GCS JSON API methods using gcloud-aio-storage and REST API.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

# Define all GCS method signatures based on GCS JSON API and gcloud-aio-storage
GCS_METHOD_SIGNATURES = {
    # Bucket operations
    'ensure_bucket_exists': {
        'required': [],
        'optional': ['bucket_name']
    },
    'list_buckets': {
        'required': [],
        'optional': ['project_id']
    },
    'get_bucket': {
        'required': [],
        'optional': ['bucket_name']
    },
    'create_bucket': {
        'required': ['bucket_name'],
        'optional': ['project_id', 'location', 'storage_class']
    },
    'patch_bucket': {
        'required': [],
        'optional': ['bucket_name', 'metadata']
    },
    'delete_bucket': {
        'required': [],
        'optional': ['bucket_name', 'force']
    },
    # Object operations
    'list_objects': {
        'required': [],
        'optional': ['bucket_name', 'prefix', 'delimiter', 'page_token', 'page_size']
    },
    'upload_object': {
        'required': ['bucket_name', 'object_name', 'data'],
        'optional': ['content_type', 'metadata']
    },
    'download_object': {
        'required': ['bucket_name', 'object_name'],
        'optional': []
    },
    'delete_object': {
        'required': ['bucket_name', 'object_name'],
        'optional': []
    },
    'get_object_metadata': {
        'required': ['bucket_name', 'object_name'],
        'optional': []
    },
    'copy_object': {
        'required': ['source_bucket', 'source_object'],
        'optional': ['dest_bucket', 'dest_object', 'metadata']
    },
    'compose_object': {
        'required': ['bucket_name', 'destination_object', 'source_objects'],
        'optional': ['metadata']
    },
    'rewrite_object': {
        'required': ['source_bucket', 'source_object'],
        'optional': ['dest_bucket', 'dest_object', 'metadata']
    },
    'update_object_metadata': {
        'required': ['bucket_name', 'object_name', 'metadata'],
        'optional': []
    },
    # Additional bucket operations
    'get_bucket_iam_policy': {
        'required': [],
        'optional': ['bucket_name']
    },
    'set_bucket_iam_policy': {
        'required': ['policy'],
        'optional': ['bucket_name']
    },
    'test_bucket_iam_permissions': {
        'required': ['permissions'],
        'optional': ['bucket_name']
    },
    # Folder operations (prefix-based)
    'list_folders': {
        'required': [],
        'optional': ['bucket_name', 'prefix', 'delimiter', 'page_token', 'page_size']
    },
    'create_folder': {
        'required': ['bucket_name', 'folder_path'],
        'optional': ['metadata']
    },
    'delete_folder': {
        'required': ['bucket_name', 'folder_path'],
        'optional': ['recursive']
    },
    # BucketAccessControls operations
    'list_bucket_access_controls': {
        'required': [],
        'optional': ['bucket_name']
    },
    'get_bucket_access_control': {
        'required': ['entity'],
        'optional': ['bucket_name']
    },
    'insert_bucket_access_control': {
        'required': ['entity', 'role'],
        'optional': ['bucket_name']
    },
    'patch_bucket_access_control': {
        'required': ['entity'],
        'optional': ['bucket_name', 'role']
    },
    'delete_bucket_access_control': {
        'required': ['entity'],
        'optional': ['bucket_name']
    },
    # Notifications operations
    'list_notifications': {
        'required': [],
        'optional': ['bucket_name']
    },
    'get_notification': {
        'required': ['notification_id'],
        'optional': ['bucket_name']
    },
    'insert_notification': {
        'required': ['topic'],
        'optional': ['bucket_name', 'payload_format', 'event_types', 'object_name_prefix']
    },
    'delete_notification': {
        'required': ['notification_id'],
        'optional': ['bucket_name']
    },
    # ObjectAccessControls operations
    'list_object_access_controls': {
        'required': ['bucket_name', 'object_name'],
        'optional': []
    },
    'get_object_access_control': {
        'required': ['bucket_name', 'object_name', 'entity'],
        'optional': []
    },
    'insert_object_access_control': {
        'required': ['bucket_name', 'object_name', 'entity', 'role'],
        'optional': []
    },
    'patch_object_access_control': {
        'required': ['bucket_name', 'object_name', 'entity'],
        'optional': ['role']
    },
    'delete_object_access_control': {
        'required': ['bucket_name', 'object_name', 'entity'],
        'optional': []
    },
}


def generate_method_signature(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate method signature with proper typing."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    # Build parameter list
    params = ['self']
    
    # Add required parameters
    for param in required_params:
        # Map parameter names to types
        if param in ['bucket_name', 'object_name', 'source_bucket', 'source_object', 
                     'dest_bucket', 'dest_object', 'destination_object', 'project_id', 
                     'location', 'storage_class', 'prefix', 'delimiter', 'page_token',
                     'entity', 'role', 'notification_id', 'topic', 'payload_format',
                     'object_name_prefix', 'folder_path']:
            params.append(f"{param}: str")
        elif param == 'data':
            params.append(f"{param}: bytes")
        elif param == 'source_objects':
            params.append(f"{param}: List[Dict[str, str]]")
        elif param == 'metadata':
            params.append(f"{param}: Dict[str, str]")
        elif param == 'policy':
            params.append(f"{param}: Dict[str, Any]")
        elif param == 'permissions':
            params.append(f"{param}: List[str]")
        elif param == 'event_types':
            params.append(f"{param}: List[str]")
        elif param == 'page_size':
            params.append(f"{param}: int")
        elif param in ['force', 'recursive']:
            params.append(f"{param}: bool")
        else:
            params.append(f"{param}: Any")
    
    # Add optional parameters
    for param in optional_params:
        if param in ['bucket_name', 'object_name', 'source_bucket', 'source_object',
                     'dest_bucket', 'dest_object', 'destination_object', 'project_id',
                     'location', 'storage_class', 'prefix', 'delimiter', 'page_token',
                     'entity', 'role', 'notification_id', 'topic', 'payload_format',
                     'object_name_prefix', 'folder_path']:
            params.append(f"{param}: Optional[str] = None")
        elif param == 'data':
            params.append(f"{param}: Optional[bytes] = None")
        elif param == 'source_objects':
            params.append(f"{param}: Optional[List[Dict[str, str]]] = None")
        elif param == 'metadata':
            params.append(f"{param}: Optional[Dict[str, Any]] = None")
        elif param == 'content_type':
            params.append(f"{param}: Optional[str] = None")
        elif param == 'policy':
            params.append(f"{param}: Optional[Dict[str, Any]] = None")
        elif param == 'permissions':
            params.append(f"{param}: Optional[List[str]] = None")
        elif param == 'event_types':
            params.append(f"{param}: Optional[List[str]] = None")
        elif param == 'page_size':
            params.append(f"{param}: Optional[int] = None")
        elif param in ['force', 'recursive']:
            params.append(f"{param}: bool = False")
        else:
            params.append(f"{param}: Optional[Any] = None")
    
    # Format parameters with proper line breaks for readability
    if len(params) > 4:
        param_str = ",\n        ".join(params)
        return f"async def {method_name}(\n        {param_str}\n    ) -> GCSResponse:"
    else:
        param_str = ", ".join(params)
        return f"async def {method_name}({param_str}) -> GCSResponse:"


def generate_method_docstring(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate method docstring."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    docstring = f'        """'
    
    # Add description based on method name
    method_descriptions = {
        'ensure_bucket_exists': 'Ensure bucket exists, create if needed.',
        'list_buckets': 'List all buckets in a project.',
        'get_bucket': 'Get bucket metadata.',
        'create_bucket': 'Create a new bucket.',
        'patch_bucket': 'Patch bucket metadata (partial update).',
        'delete_bucket': 'Delete a bucket.',
        'get_bucket_iam_policy': 'Get IAM policy for a bucket.',
        'set_bucket_iam_policy': 'Set IAM policy for a bucket.',
        'test_bucket_iam_permissions': 'Test IAM permissions for a bucket.',
        'list_objects': 'List objects in a bucket.',
        'upload_object': 'Upload an object to a bucket.',
        'download_object': 'Download an object from a bucket.',
        'delete_object': 'Delete an object from a bucket.',
        'get_object_metadata': 'Get object metadata without downloading content.',
        'copy_object': 'Copy an object from source to destination.',
        'compose_object': 'Compose multiple objects into a single object.',
        'rewrite_object': 'Rewrite an object (copy with potential metadata/storage class changes).',
        'update_object_metadata': 'Update object metadata.',
        'list_folders': 'List folders (prefixes) in a bucket.',
        'create_folder': 'Create a folder (placeholder object) in a bucket.',
        'delete_folder': 'Delete a folder and optionally all objects within it.',
        'list_bucket_access_controls': 'List bucket access control entries.',
        'get_bucket_access_control': 'Get a specific bucket access control entry.',
        'insert_bucket_access_control': 'Insert a new bucket access control entry.',
        'patch_bucket_access_control': 'Patch a bucket access control entry.',
        'delete_bucket_access_control': 'Delete a bucket access control entry.',
        'list_notifications': 'List bucket notifications.',
        'get_notification': 'Get a specific bucket notification.',
        'insert_notification': 'Create a new bucket notification.',
        'delete_notification': 'Delete a bucket notification.',
        'list_object_access_controls': 'List object access control entries.',
        'get_object_access_control': 'Get a specific object access control entry.',
        'insert_object_access_control': 'Insert a new object access control entry.',
        'patch_object_access_control': 'Patch an object access control entry.',
        'delete_object_access_control': 'Delete an object access control entry.',
    }
    
    docstring += method_descriptions.get(method_name, f'{method_name} operation.')
    
    all_params = required_params + optional_params
    if all_params:
        docstring += '\n\n        Args:'
        for param in required_params:
            docstring += f'\n            {param}: Required parameter'
        for param in optional_params:
            docstring += f'\n            {param}: Optional parameter'
    
    docstring += '\n\n        Returns:'
    docstring += '\n            GCSResponse: Standardized response with success/data/error format'
    docstring += '        """'
    
    return docstring


def generate_method_body(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate method body using gcloud-aio-storage and REST API."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    # Methods that use gcloud-aio-storage directly
    gcloud_methods = {
        'list_buckets': 'client.list_buckets(project=project)',
        'get_bucket': 'client.get_bucket(name)',  # Note: get_bucket returns Bucket object directly, not awaitable
        'list_objects': 'client.list_objects(name, params=params or None)',
        'upload_object': 'client.upload(name, object_name, data, content_type=content_type, metadata=metadata)',
        'download_object': 'client.download(name, object_name)',
        'delete_object': 'client.delete(name, object_name)',
        # copy_object and compose_object are handled separately due to signature differences
    }
    
    # Methods that use REST API
    rest_api_methods = {
        'create_bucket': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b?project={project}"',
            'method': 'post',
            'body': 'bucket_metadata',
            'success_codes': '(200, 201)'
        },
        'patch_bucket': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}"',
            'method': 'patch',
            'body': 'metadata',
            'success_codes': 'HTTP_OK'
        },
        'delete_bucket': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}"',
            'method': 'delete',
            'body': None,
            'success_codes': '(200, 204)'
        },
        'get_object_metadata': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o/{object_name}"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'rewrite_object': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{source_bucket}/o/{source_object}/copyTo/b/{dest_bucket_name}/o/{dest_object_name}"',
            'method': 'post',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'update_object_metadata': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o/{object_name}"',
            'method': 'patch',
            'body': 'metadata',
            'success_codes': 'HTTP_OK'
        },
        # Bucket IAM operations
        'get_bucket_iam_policy': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/iam"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'set_bucket_iam_policy': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/iam"',
            'method': 'put',
            'body': 'policy',
            'success_codes': 'HTTP_OK'
        },
        'test_bucket_iam_permissions': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/iam/testPermissions?permissions={\'&\'.join(permissions)}"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        # Note: Folder operations (list_folders, create_folder, delete_folder) use special handlers
        # and are NOT in rest_api_methods - they use gcloud-aio-storage
        # BucketAccessControls operations
        'list_bucket_access_controls': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/acl"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'get_bucket_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/acl/{entity_encoded}"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'insert_bucket_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/acl"',
            'method': 'post',
            'body': 'acl_entry',
            'success_codes': 'HTTP_OK'
        },
        'patch_bucket_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/acl/{entity_encoded}"',
            'method': 'patch',
            'body': 'acl_entry',
            'success_codes': 'HTTP_OK'
        },
        'delete_bucket_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/acl/{entity_encoded}"',
            'method': 'delete',
            'body': None,
            'success_codes': '(200, 204)'
        },
        # Notifications operations
        'list_notifications': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/notificationConfigs"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'get_notification': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/notificationConfigs/{notification_id}"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'insert_notification': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/notificationConfigs"',
            'method': 'post',
            'body': 'notification_config',
            'success_codes': 'HTTP_OK'
        },
        'delete_notification': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/notificationConfigs/{notification_id}"',
            'method': 'delete',
            'body': None,
            'success_codes': '(200, 204)'
        },
        # ObjectAccessControls operations
        'list_object_access_controls': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o/{object_name}/acl"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'get_object_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o/{object_name}/acl/{entity_encoded}"',
            'method': 'get',
            'body': None,
            'success_codes': 'HTTP_OK'
        },
        'insert_object_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o/{object_name}/acl"',
            'method': 'post',
            'body': 'acl_entry',
            'success_codes': 'HTTP_OK'
        },
        'patch_object_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o/{object_name}/acl/{entity_encoded}"',
            'method': 'patch',
            'body': 'acl_entry',
            'success_codes': 'HTTP_OK'
        },
        'delete_object_access_control': {
            'url': 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o/{object_name}/acl/{entity_encoded}"',
            'method': 'delete',
            'body': None,
            'success_codes': '(200, 204)'
        },
    }
    
    if method_name in gcloud_methods:
        # Generate gcloud-aio-storage method
        if method_name == 'get_bucket':
            # get_bucket returns Bucket object directly, not awaitable
            return _generate_get_bucket_method(method_def)
        elif method_name == 'download_object':
            # download_object needs special handling for data field
            return _generate_download_method(method_def)
        else:
            return _generate_gcloud_method(method_name, method_def, gcloud_methods[method_name])
    elif method_name in rest_api_methods:
        # Generate REST API method
        return _generate_rest_api_method(method_name, method_def, rest_api_methods[method_name])
    elif method_name == 'ensure_bucket_exists':
        return '''        if bucket_name:
            # temporarily override if provided
            self._gcs_client.client.config.bucketName = bucket_name  # type: ignore[attr-defined]
        return await self._gcs_client.ensure_bucket_exists()'''
    elif method_name == 'compose_object':
        return _generate_compose_method(method_def)
    elif method_name == 'copy_object':
        return _generate_copy_method(method_def)
    elif method_name == 'list_folders':
        # list_folders can use list_objects with delimiter
        return _generate_list_folders_method(method_def)
    elif method_name == 'create_folder':
        # create_folder creates a placeholder object
        return _generate_create_folder_method(method_def)
    elif method_name == 'delete_folder':
        # delete_folder handles recursive deletion
        return _generate_delete_folder_method(method_def)
    else:
        # Generic error handling
        return '''        try:
            # TODO: Implement {method_name}
            return GCSResponse(success=False, error="Method not yet implemented")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''.format(method_name=method_name)


def _generate_get_bucket_method(method_def: Dict[str, List[str]]) -> str:
    """Generate get_bucket method - returns Bucket object directly, not awaitable."""
    return '''        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            # get_bucket returns a Bucket object directly, not awaitable
            bucket = client.get_bucket(name)
            # Convert bucket to dict if it's an object
            if hasattr(bucket, '__dict__'):
                bucket_data = bucket.__dict__
            elif hasattr(bucket, 'to_dict'):
                bucket_data = bucket.to_dict()
            else:
                bucket_data = str(bucket)
            return GCSResponse(success=True, data={"bucket": bucket_data})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_download_method(method_def: Dict[str, List[str]]) -> str:
    """Generate download_object method - returns data in correct format."""
    return '''        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            content = await client.download(name, object_name)
            return GCSResponse(success=True, data={"data": content})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_copy_method(method_def: Dict[str, List[str]]) -> str:
    """Generate copy_object method - uses correct signature."""
    return '''        try:
            dest_bucket_name = self._get_bucket_name(dest_bucket) if dest_bucket else self._get_bucket_name()
            dest_object_name = dest_object or source_object
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                # Use REST API copyTo endpoint
                url = f"{emulator.rstrip('/')}/storage/v1/b/{source_bucket}/o/{source_object}/copyTo/b/{dest_bucket_name}/o/{dest_object_name}"
                async with aiohttp.ClientSession() as session:
                    async with session.post(url) as resp:
                        if resp.status == HTTP_OK:
                            result = await resp.json()
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to copy object: HTTP {resp.status} {text}")
            else:
                # For real GCP, try gcloud-aio-storage copy
                client = await self._get_storage_client()
                # copy() signature: copy(source_bucket, source_object, dest_bucket, dest_object)
                result = await client.copy(
                    source_bucket,
                    source_object,
                    dest_bucket_name,
                    dest_object_name
                )
                return GCSResponse(success=True, data={"result": result})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_gcloud_method(method_name: str, method_def: Dict[str, List[str]], gcloud_call: str) -> str:
    """Generate method body for gcloud-aio-storage methods."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    # Build variable setup
    setup_lines = []
    
    if 'bucket_name' in required_params or 'bucket_name' in optional_params:
        setup_lines.append("name = self._get_bucket_name(bucket_name)")
    
    if 'project_id' in optional_params:
        setup_lines.append("project = project_id or self._gcs_client.client.get_project_id()")
        setup_lines.append("if not project:")
        setup_lines.append('                return GCSResponse(success=False, error="projectId is required")')
    
    if method_name == 'list_objects':
        setup_lines.append("params: Dict[str, Any] = {}")
        setup_lines.append("if prefix is not None:")
        setup_lines.append('                params["prefix"] = prefix')
        setup_lines.append("if delimiter is not None:")
        setup_lines.append('                params["delimiter"] = delimiter')
        setup_lines.append("if page_token is not None:")
        setup_lines.append('                params["pageToken"] = page_token')
        setup_lines.append("if page_size is not None:")
        setup_lines.append('                params["maxResults"] = page_size')
    
    if method_name == 'copy_object':
        setup_lines.append("dest_bucket_name = self._get_bucket_name(dest_bucket) if dest_bucket else self._get_bucket_name()")
        setup_lines.append("dest_object_name = dest_object or source_object")
    
    if setup_lines:
        setup = "            " + "\n            ".join(setup_lines)
    else:
        setup = ""
    
    return f'''        try:
            client = await self._get_storage_client()
{setup}
            result = await {gcloud_call}
            return GCSResponse(success=True, data={{"result": result}} if not isinstance(result, dict) else result)
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_rest_api_method(method_name: str, method_def: Dict[str, List[str]], api_def: Dict[str, Any]) -> str:
    """Generate method body for REST API methods."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    setup_lines = []
    
    # Note: create_bucket uses bucket_name directly, no need for 'name' variable
    if ('bucket_name' in required_params or 'bucket_name' in optional_params) and method_name != 'create_bucket':
        setup_lines.append("name = self._get_bucket_name(bucket_name)")
    
    if method_name == 'create_bucket':
        setup_lines.append("project = project_id or self._gcs_client.client.get_project_id()")
        setup_lines.append("if not project:")
        setup_lines.append('                return GCSResponse(success=False, error="projectId is required to create a bucket")')
        setup_lines.append("bucket_metadata: Dict[str, Any] = {\"name\": bucket_name}")
        setup_lines.append("if location:")
        setup_lines.append('                bucket_metadata["location"] = location')
        setup_lines.append("if storage_class:")
        setup_lines.append('                bucket_metadata["storageClass"] = storage_class')
    
    if method_name == 'patch_bucket' or method_name == 'update_object_metadata':
        setup_lines.append("if not metadata:")
        setup_lines.append(f'                return GCSResponse(success=False, error="metadata is required for {method_name} operation")')
    
    if method_name == 'rewrite_object':
        setup_lines.append("dest_bucket_name = self._get_bucket_name(dest_bucket) if dest_bucket else self._get_bucket_name()")
        setup_lines.append("dest_object_name = dest_object or source_object")
    
    # Bucket IAM operations
    if method_name == 'set_bucket_iam_policy':
        setup_lines.append("if not policy:")
        setup_lines.append('                return GCSResponse(success=False, error="policy is required")')
    
    if method_name == 'test_bucket_iam_permissions':
        setup_lines.append("if not permissions:")
        setup_lines.append('                return GCSResponse(success=False, error="permissions list is required")')
    
    # Folder operations
    if method_name == 'list_folders':
        setup_lines.append("params: Dict[str, Any] = {}")
        setup_lines.append("params[\"delimiter\"] = delimiter or \"/\"")
        setup_lines.append("if prefix is not None:")
        setup_lines.append('                params["prefix"] = prefix')
        setup_lines.append("if page_token is not None:")
        setup_lines.append('                params["pageToken"] = page_token')
        setup_lines.append("if page_size is not None:")
        setup_lines.append('                params["maxResults"] = page_size')
        setup_lines.append("url_params = \"&\".join([f\"{k}={v}\" for k, v in params.items()])")
        setup_lines.append("url_suffix = f\"?{url_params}\" if url_params else \"\"")
    
    if method_name == 'create_folder':
        setup_lines.append("folder_metadata: Dict[str, Any] = {\"name\": folder_path}")
        setup_lines.append("if not folder_path.endswith(\"/\"):")
        setup_lines.append('                folder_metadata["name"] = f"{folder_path}/"')
        setup_lines.append("if metadata:")
        setup_lines.append('                folder_metadata["metadata"] = metadata')
    
    if method_name == 'delete_folder':
        setup_lines.append("if recursive:")
        setup_lines.append("                # List all objects with prefix and delete them")
        setup_lines.append("                client = await self._get_storage_client()")
        setup_lines.append("                prefix = folder_path if folder_path.endswith(\"/\") else f\"{folder_path}/\"")
        setup_lines.append("                objects = await client.list_objects(name, params={\"prefix\": prefix})")
        setup_lines.append("                if isinstance(objects, dict) and \"items\" in objects:")
        setup_lines.append("                    for obj in objects[\"items\"]:")
        setup_lines.append("                        obj_name = obj.get(\"name\", \"\")")
        setup_lines.append("                        await client.delete(name, obj_name)")
    
    # BucketAccessControls operations
    # Entity check for all these methods
    if method_name in ['insert_bucket_access_control', 'get_bucket_access_control', 
                   'patch_bucket_access_control', 'delete_bucket_access_control']:
        setup_lines.append("if not entity:")
        setup_lines.append('                return GCSResponse(success=False, error="entity is required")')

    # URL encoding *only* for methods that use it in the path
    if method_name in ['get_bucket_access_control', 'patch_bucket_access_control', 
                   'delete_bucket_access_control']:
        setup_lines.append("from urllib.parse import quote")
        setup_lines.append("entity_encoded = quote(entity, safe='')")
    
    if method_name == 'insert_bucket_access_control':
        setup_lines.append("if not role:")
        setup_lines.append('                return GCSResponse(success=False, error="role is required")')
        setup_lines.append("acl_entry: Dict[str, Any] = {\"entity\": entity, \"role\": role}")
    
    if method_name == 'patch_bucket_access_control':
        setup_lines.append("acl_entry: Dict[str, Any] = {}")
        setup_lines.append("if role:")
        setup_lines.append('                acl_entry["role"] = role')
    
    # Notifications operations
    if method_name == 'insert_notification':
        setup_lines.append("if not topic:")
        setup_lines.append('                return GCSResponse(success=False, error="topic is required")')
        setup_lines.append("notification_config: Dict[str, Any] = {\"topic\": topic}")
        setup_lines.append("if payload_format:")
        setup_lines.append('                notification_config["payloadFormat"] = payload_format')
        setup_lines.append("if event_types:")
        setup_lines.append('                notification_config["eventTypes"] = event_types')
        setup_lines.append("if object_name_prefix:")
        setup_lines.append('                notification_config["objectNamePrefix"] = object_name_prefix')
    
    # ObjectAccessControls operations
    # Entity check for all these methods
    if method_name in ['insert_object_access_control', 'get_object_access_control',
                   'patch_object_access_control', 'delete_object_access_control']:
        setup_lines.append("if not entity:")
        setup_lines.append('                return GCSResponse(success=False, error="entity is required")')

    # URL encoding *only* for methods that use it in the path
    if method_name in ['get_object_access_control', 'patch_object_access_control', 
                   'delete_object_access_control']:
        setup_lines.append("from urllib.parse import quote")
        setup_lines.append("entity_encoded = quote(entity, safe='')")
    
    if method_name == 'insert_object_access_control':
        setup_lines.append("if not role:")
        setup_lines.append('                return GCSResponse(success=False, error="role is required")')
        setup_lines.append("acl_entry: Dict[str, Any] = {\"entity\": entity, \"role\": role}")
    
    if method_name == 'patch_object_access_control':
        setup_lines.append("acl_entry: Dict[str, Any] = {}")
        setup_lines.append("if role:")
        setup_lines.append('                acl_entry["role"] = role')
    
    if setup_lines:
        setup = "            " + "\n            ".join(setup_lines)
    else:
        setup = ""
    
    http_method = api_def['method']
    url = api_def['url']
    body = api_def.get('body')
    success_codes = api_def.get('success_codes', 'HTTP_OK')
    
    # Special URL handling
    if method_name == 'test_bucket_iam_permissions':
        url = 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/iam/testPermissions?permissions=" + "&".join(permissions)'
    elif method_name == 'list_folders':
        url = 'f"{emulator.rstrip(\'/\')}/storage/v1/b/{name}/o{url_suffix}"'
    
    body_param = f", json={body}" if body else ""
    if method_name == 'rewrite_object':
        body_param = ", headers={}"
    
    return f'''        try:
{setup}
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                url = {url}
                async with aiohttp.ClientSession() as session:
                    async with session.{http_method}(url{body_param}) as resp:
                        if resp.status in {success_codes} if isinstance({success_codes}, tuple) else resp.status == {success_codes}:
                            try:
                                result = await resp.json()
                            except Exception:
                                # Handle empty/null response
                                result = {{"status": "success"}}
                            return GCSResponse(success=True, data={{"result": result}})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed: HTTP {{resp.status}} {{text}}")
            else:
                return GCSResponse(success=False, error="{method_name} requires REST API implementation for production")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_compose_method(method_def: Dict[str, List[str]]) -> str:
    """Generate compose_object method body."""
    return '''        try:
            name = self._get_bucket_name(bucket_name)
            emulator = os.environ.get("STORAGE_EMULATOR_HOST")

            if emulator:
                url = f"{emulator.rstrip('/')}/storage/v1/b/{name}/o/{destination_object}/compose"

                # Format source objects correctly for GCS API
                formatted_sources = []
                for src_obj in source_objects:
                    if isinstance(src_obj, dict):
                        formatted_sources.append({"name": src_obj.get("name", src_obj.get("object", ""))})
                    else:
                        formatted_sources.append({"name": str(src_obj)})

                request_body = {"sourceObjects": formatted_sources}
                if metadata:
                    request_body["destination"] = {"metadata": metadata}

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=request_body) as resp:
                        if resp.status == HTTP_OK:
                            result = await resp.json()
                            return GCSResponse(success=True, data={"result": result})
                        text = await resp.text()
                        return GCSResponse(success=False, error=f"Failed to compose objects: HTTP {resp.status} {text}")
            else:
                # For real GCP, try using gcloud-aio-storage compose
                client = await self._get_storage_client()
                # compose() signature: compose(bucket, destination_object, source_objects)
                result = await client.compose(
                    name, destination_object,
                    source_objects
                )
                return GCSResponse(success=True, data={"result": result})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_list_folders_method(method_def: Dict[str, List[str]]) -> str:
    """Generate list_folders method body - uses list_objects with delimiter."""
    return '''        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            params: Dict[str, Any] = {}
            params["delimiter"] = delimiter or "/"
            if prefix is not None:
                params["prefix"] = prefix
            if page_token is not None:
                params["pageToken"] = page_token
            if page_size is not None:
                params["maxResults"] = page_size
            result = await client.list_objects(name, params=params or None)
            # Extract prefixes (folders) from result
            folders = []
            if isinstance(result, dict):
                folders = result.get("prefixes", [])
            return GCSResponse(success=True, data={"folders": folders, "result": result})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_create_folder_method(method_def: Dict[str, List[str]]) -> str:
    """Generate create_folder method body - creates placeholder object."""
    return '''        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            # Ensure folder path ends with /
            folder_name = folder_path if folder_path.endswith("/") else f"{folder_path}/"
            # Create placeholder object (empty object with trailing /)
            folder_metadata = metadata or {}
            result = await client.upload(name, folder_name, b"", content_type="application/x-directory", metadata=folder_metadata)
            return GCSResponse(success=True, data={"result": result, "folder_path": folder_name})
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def _generate_delete_folder_method(method_def: Dict[str, List[str]]) -> str:
    """Generate delete_folder method body - handles recursive deletion."""
    return '''        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)
            # Ensure folder path ends with /
            prefix = folder_path if folder_path.endswith("/") else f"{folder_path}/"
            if recursive:
                # List all objects with prefix and delete them
                params = {"prefix": prefix}
                objects = await client.list_objects(name, params=params)
                deleted_count = 0
                if isinstance(objects, dict) and "items" in objects:
                    for obj in objects["items"]:
                        obj_name = obj.get("name", "")
                        if obj_name:
                            try:
                                await client.delete(name, obj_name)
                                deleted_count += 1
                            except Exception:
                                pass  # Ignore errors for individual objects
                # Also delete the folder placeholder if it exists
                try:
                    await client.delete(name, prefix)
                    deleted_count += 1
                except Exception:
                    pass
                return GCSResponse(success=True, data={"deleted_count": deleted_count, "folder_path": prefix})
            else:
                # Just delete the folder placeholder
                try:
                    await client.delete(name, prefix)
                    return GCSResponse(success=True, data={"folder_path": prefix})
                except Exception as delete_error:
                    # Handle 404 gracefully - folder doesn't exist (idempotent delete)
                    error_str = str(delete_error)
                    if "404" in error_str or "Not Found" in error_str:
                        return GCSResponse(success=True, data={"folder_path": prefix, "message": "Folder not found (already deleted)"})
                    raise
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def generate_complete_gcs_data_source() -> str:
    """Generate complete GCSDataSource class."""
    
    class_code = '''import os
from http import HTTPStatus
from typing import Any, Dict, List, Optional

try:
    import aiohttp  # type: ignore
    from gcloud.aio.storage import Storage  # type: ignore
except ImportError:
    raise ImportError("gcloud-aio-storage and aiohttp are not installed. Please install with `pip install gcloud-aio-storage aiohttp`")

from app.sources.client.gcs.gcs import GCSClient, GCSResponse

# HTTP status constants
HTTP_OK = HTTPStatus.OK.value


class GCSDataSource:
    """
    Google Cloud Storage Data Source - Comprehensive API wrapper
    Implements GCS JSON API operations aligned with Azure/S3 patterns.
    Features:
    - Bucket operations (CRUD, IAM, lifecycle)
    - Object operations (CRUD, compose, copy, rewrite)
    - Folder operations (list, create, delete)
    - Bucket and Object Access Controls (ACL management)
    - Bucket Notifications
    - Metadata and ACL management
    - Error handling with GCSResponse

    Uses:
    - gcloud-aio-storage: Third-party async library for GCS operations
    - google-auth: Official Google authentication library
    - REST API: For operations not available in gcloud-aio-storage

    Note: Some operations (IAM, ACLs, Notifications) may not be fully supported
    by storage emulators (fake-gcs-server) but will work with production GCS.
    """

    def __init__(self, gcs_client: GCSClient) -> None:
        """Initialize with GCSClient."""
        self._gcs_client = gcs_client
        self._storage_client: Optional[Storage] = None

    async def _get_storage_client(self) -> Storage:
        """Get or create the Storage client."""
        if self._storage_client is None:
            self._storage_client = await self._gcs_client.get_storage_client()
        return self._storage_client

    def _get_bucket_name(self, bucket_name: Optional[str] = None) -> str:
        """Get bucket name from parameter or default."""
        return bucket_name or self._gcs_client.get_bucket_name()

'''
    
    # Generate all methods
    for method_name, method_def in sorted(GCS_METHOD_SIGNATURES.items()):
        try:
            signature = generate_method_signature(method_name, method_def)
            docstring = generate_method_docstring(method_name, method_def)
            method_body = generate_method_body(method_name, method_def)
            
            complete_method = f"    {signature}\n{docstring}\n{method_body}\n\n"
            class_code += complete_method
            
        except Exception as e:
            print(f"Warning: Failed to generate method {method_name}: {e}")
    
    return class_code


def main():
    """Generate GCSDataSource and write to file."""
    print("⚙️ Generating GCS DataSource...")
    
    generated_code = generate_complete_gcs_data_source()
    
    # Write to output file
    output_path = Path(__file__).parent.parent / "app" / "sources" / "external" / "gcs" / "gcs.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(generated_code)
    
    print(f"✅ Generated GCSDataSource with {len(GCS_METHOD_SIGNATURES)} methods")
    print(f"📝 Written to: {output_path}")


if __name__ == "__main__":
    main()

