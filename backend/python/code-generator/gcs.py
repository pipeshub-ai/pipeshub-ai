# ruff: noqa
"""
Complete GCS Data Source Generator
Generates GCSDataSource class with ALL GCS JSON API methods using the OFFICIAL google-cloud-storage SDK.
"""

import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

# Define all GCS method signatures
GCS_METHOD_SIGNATURES = {
    # Bucket operations
    'ensure_bucket_exists': {'required': [], 'optional': ['bucket_name']},
    'list_buckets': {'required': [], 'optional': ['project_id']},
    'get_bucket': {'required': [], 'optional': ['bucket_name']},
    'create_bucket': {'required': ['bucket_name'], 'optional': ['project_id', 'location', 'storage_class']},
    'patch_bucket': {'required': [], 'optional': ['bucket_name', 'metadata']},
    'delete_bucket': {'required': [], 'optional': ['bucket_name', 'force']},
    # Object operations
    'list_objects': {'required': [], 'optional': ['bucket_name', 'prefix', 'delimiter', 'page_token', 'page_size']},
    'upload_object': {'required': ['bucket_name', 'object_name', 'data'], 'optional': ['content_type', 'metadata']},
    'download_object': {'required': ['bucket_name', 'object_name'], 'optional': []},
    'delete_object': {'required': ['bucket_name', 'object_name'], 'optional': []},
    'get_object_metadata': {'required': ['bucket_name', 'object_name'], 'optional': []},
    'copy_object': {'required': ['source_bucket', 'source_object'], 'optional': ['dest_bucket', 'dest_object', 'metadata']},
    'compose_object': {'required': ['bucket_name', 'destination_object', 'source_objects'], 'optional': ['metadata']},
    'rewrite_object': {'required': ['source_bucket', 'source_object'], 'optional': ['dest_bucket', 'dest_object', 'metadata']},
    'update_object_metadata': {'required': ['bucket_name', 'object_name', 'metadata'], 'optional': []},
    # Folder operations
    'create_folder': {'required': ['bucket_name', 'folder_path'], 'optional': ['metadata']},
    'list_folders': {'required': [], 'optional': ['bucket_name', 'prefix', 'delimiter']},
    'delete_folder': {'required': ['bucket_name', 'folder_path'], 'optional': ['recursive']},
    # IAM
    'get_bucket_iam_policy': {'required': [], 'optional': ['bucket_name']},
    'set_bucket_iam_policy': {'required': ['policy'], 'optional': ['bucket_name']},
    'test_bucket_iam_permissions': {'required': ['permissions'], 'optional': ['bucket_name']},
    # ACLs
    'list_bucket_access_controls': {'required': [], 'optional': ['bucket_name']},
    'get_bucket_access_control': {'required': ['entity'], 'optional': ['bucket_name']},
    'insert_bucket_access_control': {'required': ['entity', 'role'], 'optional': ['bucket_name']},
    'patch_bucket_access_control': {'required': ['entity'], 'optional': ['bucket_name', 'role']},
    'delete_bucket_access_control': {'required': ['entity'], 'optional': ['bucket_name']},
    'list_object_access_controls': {'required': ['bucket_name', 'object_name'], 'optional': []},
    'get_object_access_control': {'required': ['bucket_name', 'object_name', 'entity'], 'optional': []},
    'insert_object_access_control': {'required': ['bucket_name', 'object_name', 'entity', 'role'], 'optional': []},
    'patch_object_access_control': {'required': ['bucket_name', 'object_name', 'entity'], 'optional': ['role']},
    'delete_object_access_control': {'required': ['bucket_name', 'object_name', 'entity'], 'optional': []},
    # Notifications
    'list_notifications': {'required': [], 'optional': ['bucket_name']},
    'get_notification': {'required': ['notification_id'], 'optional': ['bucket_name']},
    'insert_notification': {'required': ['topic'], 'optional': ['bucket_name', 'payload_format', 'event_types', 'object_name_prefix']},
    'delete_notification': {'required': ['notification_id'], 'optional': ['bucket_name']},
}


def generate_method_signature(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate method signature with proper typing."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    params = ['self']
    
    for param in required_params:
        if param == 'data':
            params.append(f"{param}: bytes")
        elif param == 'source_objects':
            params.append(f"{param}: List[str]")
        elif param == 'metadata':
            params.append(f"{param}: Dict[str, str]")
        elif param == 'policy':
             params.append(f"{param}: Dict[str, Any]")
        elif param in ['force', 'recursive']:
            params.append(f"{param}: bool")
        elif param in ['permissions', 'event_types']:
             params.append(f"{param}: List[str]")
        elif param == 'page_size':
             params.append(f"{param}: int")
        else:
            params.append(f"{param}: str")
    
    for param in optional_params:
        if param == 'data':
            params.append(f"{param}: Optional[bytes] = None")
        elif param == 'metadata':
            params.append(f"{param}: Optional[Dict[str, Any]] = None")
        elif param == 'policy':
             params.append(f"{param}: Optional[Dict[str, Any]] = None")
        elif param in ['force', 'recursive']:
            params.append(f"{param}: bool = False")
        elif param in ['permissions', 'event_types']:
             params.append(f"{param}: Optional[List[str]] = None")
        elif param == 'page_size':
             params.append(f"{param}: Optional[int] = None")
        else:
            params.append(f"{param}: Optional[str] = None")
    
    param_str = ", ".join(params)
    return f"async def {method_name}({param_str}) -> GCSResponse:"


def generate_method_body(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate method body using official google-cloud-storage SDK."""
    
    common_setup = """
        try:
            client = await self._get_storage_client()
            name = self._get_bucket_name(bucket_name)"""
    
    # --- Core Operations ---
    if method_name == 'ensure_bucket_exists':
        return '''        if bucket_name:
            self._gcs_client.client.config.bucketName = bucket_name
        return await self._gcs_client.ensure_bucket_exists()'''

    elif method_name == 'list_buckets':
        return '''        try:
            client = await self._get_storage_client()
            buckets = list(client.list_buckets())
            bucket_names = [b.name for b in buckets]
            return GCSResponse(success=True, data={"buckets": bucket_names})
        except Exception as e:
            logger.error(f"Error in list_buckets: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'get_bucket':
        return common_setup + '''
            bucket = client.bucket(name)
            if not bucket.exists():
                return GCSResponse(success=False, error=f"Bucket {name} not found")
            bucket.reload()
            return GCSResponse(success=True, data={"bucket": bucket.name, "location": bucket.location})
        except Exception as e:
            logger.error(f"Error in get_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'create_bucket':
        return '''        try:
            client = await self._get_storage_client()
            bucket = client.bucket(bucket_name)
            if location:
                bucket.location = location
            if storage_class:
                bucket.storage_class = storage_class
            bucket.create(project=project_id)
            return GCSResponse(success=True, data={"bucket": bucket.name, "action": "created"})
        except Exception as e:
            logger.error(f"Error in create_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'patch_bucket':
        return common_setup + '''
            bucket = client.bucket(name)
            if metadata:
                for key, value in metadata.items():
                    setattr(bucket, key, value)
                bucket.patch()
            return GCSResponse(success=True, data={"bucket": name, "action": "patched"})
        except Exception as e:
            logger.error(f"Error in patch_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'delete_bucket':
        return common_setup + '''
            bucket = client.bucket(name)
            if force:
                blobs = list(client.list_blobs(name))
                for blob in blobs:
                    blob.delete()
            bucket.delete(force=force)
            return GCSResponse(success=True, data={"bucket": name, "action": "deleted"})
        except Exception as e:
            logger.error(f"Error in delete_bucket: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'list_objects':
        return common_setup + '''
            blobs = client.list_blobs(name, prefix=prefix, delimiter=delimiter)
            items = [blob.name for blob in blobs]
            return GCSResponse(success=True, data={"items": items, "prefixes": list(blobs.prefixes)})
        except Exception as e:
            logger.error(f"Error in list_objects: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'upload_object':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.upload_from_string(data, content_type=content_type)
            if metadata:
                blob.metadata = metadata
                blob.patch()
            return GCSResponse(success=True, data={"bucket": name, "object": object_name, "action": "uploaded"})
        except Exception as e:
            logger.error(f"Error in upload_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'download_object':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            content = blob.download_as_bytes()
            return GCSResponse(success=True, data={"data": content})
        except Exception as e:
            logger.error(f"Error in download_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'delete_object':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.delete()
            return GCSResponse(success=True, data={"bucket": name, "object": object_name, "action": "deleted"})
        except Exception as e:
            logger.error(f"Error in delete_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'get_object_metadata':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.get_blob(object_name)
            if not blob:
                return GCSResponse(success=False, error=f"Object {object_name} not found")
            return GCSResponse(success=True, data={"metadata": blob.metadata, "content_type": blob.content_type, "size": blob.size})
        except Exception as e:
            logger.error(f"Error in get_object_metadata: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
            
    elif method_name == 'copy_object' or method_name == 'rewrite_object':
        return '''        try:
            client = await self._get_storage_client()
            s_bucket = client.bucket(source_bucket)
            s_blob = s_bucket.blob(source_object)
            d_bucket_name = self._get_bucket_name(dest_bucket)
            d_bucket = client.bucket(d_bucket_name)
            d_object_name = dest_object or source_object
            s_bucket.copy_blob(s_blob, d_bucket, d_object_name)
            return GCSResponse(success=True, data={"source": source_object, "destination": d_object_name})
        except Exception as e:
            logger.error(f"Error in copy/rewrite: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'compose_object':
        return common_setup + '''
            bucket = client.bucket(name)
            destination = bucket.blob(destination_object)
            sources = [bucket.blob(s) for s in source_objects]
            destination.compose(sources)
            return GCSResponse(success=True, data={"composed": destination_object})
        except Exception as e:
            logger.error(f"Error in compose_object: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
    
    elif method_name == 'update_object_metadata':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.get_blob(object_name)
            if blob:
                blob.metadata = metadata
                blob.patch()
                return GCSResponse(success=True, data={"object": object_name, "action": "metadata_updated"})
            return GCSResponse(success=False, error="Object not found")
        except Exception as e:
            logger.error(f"Error in update_object_metadata: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    # --- Folder Operations ---       
    elif method_name == 'create_folder':
        return common_setup + '''
            folder_name = folder_path if folder_path.endswith("/") else f"{folder_path}/"
            bucket = client.bucket(name)
            blob = bucket.blob(folder_name)
            blob.upload_from_string(b"", content_type="application/x-directory")
            return GCSResponse(success=True, data={"folder": folder_name, "action": "created"})
        except Exception as e:
            logger.error(f"Error in create_folder: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
            
    elif method_name == 'list_folders':
        return common_setup + '''
            blobs = client.list_blobs(name, prefix=prefix, delimiter=delimiter or '/')
            # Note: .prefixes is populated only AFTER iterating blobs
            _ = [blob.name for blob in blobs]
            return GCSResponse(success=True, data={"folders": list(blobs.prefixes)})
        except Exception as e:
            logger.error(f"Error in list_folders: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'delete_folder':
        return common_setup + '''
            prefix = folder_path if folder_path.endswith("/") else f"{folder_path}/"
            if recursive:
                blobs = list(client.list_blobs(name, prefix=prefix))
                for blob in blobs:
                    blob.delete()
            else:
                try:
                    bucket = client.bucket(name)
                    blob = bucket.blob(prefix)
                    blob.delete()
                except NotFound:
                    return GCSResponse(success=True, data={"folder": prefix, "message": "Folder not found"})
            return GCSResponse(success=True, data={"folder": prefix, "action": "deleted"})
        except Exception as e:
            logger.error(f"Error in delete_folder: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    # --- IAM Operations ---
    elif method_name == 'get_bucket_iam_policy':
        return common_setup + '''
            bucket = client.bucket(name)
            policy = bucket.get_iam_policy()
            return GCSResponse(success=True, data={"policy": policy.to_api_repr()})
        except Exception as e:
            logger.error(f"Error in get_bucket_iam_policy: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'set_bucket_iam_policy':
        return common_setup + '''
            bucket = client.bucket(name)
            from google.api_core.iam import Policy
            new_policy = Policy.from_api_repr(policy)
            bucket.set_iam_policy(new_policy)
            return GCSResponse(success=True, data={"action": "iam_policy_set"})
        except Exception as e:
            logger.error(f"Error in set_bucket_iam_policy: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'test_bucket_iam_permissions':
        return common_setup + '''
            bucket = client.bucket(name)
            permissions_allowed = bucket.test_iam_permissions(permissions)
            return GCSResponse(success=True, data={"permissions": permissions_allowed})
        except Exception as e:
            logger.error(f"Error in test_bucket_iam_permissions: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    # --- ACL Operations ---
    elif method_name == 'list_bucket_access_controls':
        return common_setup + '''
            bucket = client.bucket(name)
            acls = list(bucket.acl)
            return GCSResponse(success=True, data={"items": [str(acl) for acl in acls]})
        except Exception as e:
            logger.error(f"Error in list_bucket_access_controls: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'insert_bucket_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            bucket.acl.entity_from_dict({"entity": entity, "role": role})
            bucket.acl.save()
            return GCSResponse(success=True, data={"action": "acl_inserted"})
        except Exception as e:
            logger.error(f"Error in insert_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
            
    elif method_name == 'get_bucket_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            # Manually iterate to find entity
            acls = list(bucket.acl)
            found = [str(a) for a in acls if entity in str(a)]
            if found:
                 return GCSResponse(success=True, data={"acl": found[0]})
            return GCSResponse(success=False, error="ACL entity not found")
        except Exception as e:
            logger.error(f"Error in get_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'patch_bucket_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            bucket.acl.entity_from_dict({"entity": entity, "role": role})
            bucket.acl.save()
            return GCSResponse(success=True, data={"action": "acl_patched"})
        except Exception as e:
            logger.error(f"Error in patch_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'delete_bucket_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            bucket.acl.reload()
            bucket.acl.revoke(entity)
            bucket.acl.save()
            return GCSResponse(success=True, data={"action": "acl_deleted"})
        except Exception as e:
            logger.error(f"Error in delete_bucket_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    # --- Object ACLs ---
    elif method_name == 'list_object_access_controls':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            acls = list(blob.acl)
            return GCSResponse(success=True, data={"items": [str(acl) for acl in acls]})
        except Exception as e:
            logger.error(f"Error in list_object_access_controls: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'insert_object_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.acl.entity_from_dict({"entity": entity, "role": role})
            blob.acl.save()
            return GCSResponse(success=True, data={"action": "acl_inserted"})
        except Exception as e:
            logger.error(f"Error in insert_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'get_object_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            acls = list(blob.acl)
            found = [str(a) for a in acls if entity in str(a)]
            if found:
                 return GCSResponse(success=True, data={"acl": found[0]})
            return GCSResponse(success=False, error="ACL entity not found")
        except Exception as e:
            logger.error(f"Error in get_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    elif method_name == 'patch_object_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.acl.entity_from_dict({"entity": entity, "role": role})
            blob.acl.save()
            return GCSResponse(success=True, data={"action": "acl_patched"})
        except Exception as e:
            logger.error(f"Error in patch_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
            
    elif method_name == 'delete_object_access_control':
        return common_setup + '''
            bucket = client.bucket(name)
            blob = bucket.blob(object_name)
            blob.acl.reload()
            blob.acl.revoke(entity)
            blob.acl.save()
            return GCSResponse(success=True, data={"action": "acl_deleted"})
        except Exception as e:
            logger.error(f"Error in delete_object_access_control: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    # --- Notifications ---
    elif method_name == 'list_notifications':
        return common_setup + '''
            bucket = client.bucket(name)
            notifs = list(bucket.list_notifications())
            return GCSResponse(success=True, data={"items": [str(n) for n in notifs]})
        except Exception as e:
            logger.error(f"Error in list_notifications: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
    
    elif method_name == 'insert_notification':
        return common_setup + '''
            bucket = client.bucket(name)
            notif = bucket.notification(topic_name=topic, custom_attributes=None, payload_format=payload_format)
            notif.create()
            return GCSResponse(success=True, data={"action": "notification_created"})
        except Exception as e:
            logger.error(f"Error in insert_notification: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
            
    elif method_name == 'get_notification':
        return common_setup + '''
            bucket = client.bucket(name)
            notif = bucket.get_notification(notification_id)
            return GCSResponse(success=True, data={"notification": str(notif)})
        except Exception as e:
            logger.error(f"Error in get_notification: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''
            
    elif method_name == 'delete_notification':
        return common_setup + '''
            bucket = client.bucket(name)
            notif = bucket.get_notification(notification_id)
            notif.delete()
            return GCSResponse(success=True, data={"action": "notification_deleted"})
        except Exception as e:
            logger.error(f"Error in delete_notification: {e}", exc_info=True)
            return GCSResponse(success=False, error=str(e))'''

    else:
        return f'''        try:
            # TODO: Implement {method_name} using official SDK
            return GCSResponse(success=False, error="Method {method_name} not implemented yet")
        except Exception as e:
            return GCSResponse(success=False, error=str(e))'''


def generate_complete_gcs_data_source() -> str:
    """Generate complete GCSDataSource class."""
    
    class_code = '''import logging
from typing import Any, Dict, List, Optional

try:
    from google.cloud import storage  # type: ignore
    from google.cloud.exceptions import NotFound  # type: ignore
except ImportError:
    raise ImportError("google-cloud-storage is not installed. Please install with `pip install google-cloud-storage`")

from app.sources.client.gcs.gcs import GCSClient, GCSResponse

# Set up logger
logger = logging.getLogger(__name__)


class GCSDataSource:
    """
    Google Cloud Storage Data Source - API wrapper using official SDK.
    Implements GCS operations aligned with Azure/S3 patterns.

    Features:
    - Uses official google-cloud-storage SDK
    - Handles both Production and Emulator environments transparently
    """

    def __init__(self, gcs_client: GCSClient) -> None:
        """Initialize with GCSClient."""
        self._gcs_client = gcs_client
        self._storage_client: Optional[storage.Client] = None

    async def _get_storage_client(self) -> storage.Client:
        """Get or create the Storage client."""
        if self._storage_client is None:
            self._storage_client = await self._gcs_client.get_storage_client()
        return self._storage_client

    def _get_bucket_name(self, bucket_name: Optional[str] = None) -> str:
        """Get bucket name from parameter or default."""
        return bucket_name or self._gcs_client.get_bucket_name()

'''
    
    for method_name, method_def in sorted(GCS_METHOD_SIGNATURES.items()):
        try:
            signature = generate_method_signature(method_name, method_def)
            method_body = generate_method_body(method_name, method_def)
            docstring = f'        """{method_name} operation."""'
            complete_method = f"    {signature}\n{docstring}\n{method_body}\n\n"
            class_code += complete_method
        except Exception as e:
            print(f"Warning: Failed to generate method {method_name}: {e}")
    
    return class_code


def main():
    print("⚙️ Generating GCS DataSource (Official SDK)...")
    generated_code = generate_complete_gcs_data_source()
    output_path = Path(__file__).parent.parent / "app" / "sources" / "external" / "gcs" / "gcs.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(generated_code)
    print(f"✅ Generated GCSDataSource to: {output_path}")

if __name__ == "__main__":
    main()