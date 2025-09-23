# ruff: noqa
"""
Complete S3 Data Source Generator with EXPLICIT METHOD SIGNATURES
Generates S3DataSource class with ALL boto3 S3 methods with proper explicit parameters.
"""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

# Define all S3 method signatures with proper parameters
S3_METHOD_SIGNATURES = {
    'abort_multipart_upload': {
        'required': ['Bucket', 'Key', 'UploadId'],
        'optional': ['RequestPayer', 'ExpectedBucketOwner']
    },
    'complete_multipart_upload': {
        'required': ['Bucket', 'Key', 'UploadId'],
        'optional': ['MultipartUpload', 'RequestPayer', 'ExpectedBucketOwner', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5']
    },
    'copy_object': {
        'required': ['Bucket', 'CopySource', 'Key'],
        'optional': ['ACL', 'CacheControl', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage', 'ContentType', 'CopySourceIfMatch', 'CopySourceIfModifiedSince', 'CopySourceIfNoneMatch', 'CopySourceIfUnmodifiedSince', 'Expires', 'GrantFullControl', 'GrantRead', 'GrantReadACP', 'GrantWriteACP', 'Metadata', 'MetadataDirective', 'TaggingDirective', 'ServerSideEncryption', 'StorageClass', 'WebsiteRedirectLocation', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'SSEKMSKeyId', 'SSEKMSEncryptionContext', 'BucketKeyEnabled', 'CopySourceSSECustomerAlgorithm', 'CopySourceSSECustomerKey', 'CopySourceSSECustomerKeyMD5', 'RequestPayer', 'Tagging', 'ObjectLockMode', 'ObjectLockRetainUntilDate', 'ObjectLockLegalHoldStatus', 'ExpectedBucketOwner', 'ExpectedSourceBucketOwner']
    },
    'create_bucket': {
        'required': ['Bucket'],
        'optional': ['ACL', 'CreateBucketConfiguration', 'GrantFullControl', 'GrantRead', 'GrantReadACP', 'GrantWrite', 'GrantWriteACP', 'ObjectLockEnabledForBucket', 'ObjectOwnership']
    },
    'create_multipart_upload': {
        'required': ['Bucket', 'Key'],
        'optional': ['ACL', 'CacheControl', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage', 'ContentType', 'Expires', 'GrantFullControl', 'GrantRead', 'GrantReadACP', 'GrantWriteACP', 'Metadata', 'ServerSideEncryption', 'StorageClass', 'WebsiteRedirectLocation', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'SSEKMSKeyId', 'SSEKMSEncryptionContext', 'BucketKeyEnabled', 'RequestPayer', 'Tagging', 'ObjectLockMode', 'ObjectLockRetainUntilDate', 'ObjectLockLegalHoldStatus', 'ExpectedBucketOwner', 'ChecksumAlgorithm']
    },
    'delete_bucket': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_analytics_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_cors': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_encryption': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_intelligent_tiering_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_inventory_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_lifecycle': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_metrics_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_ownership_controls': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_policy': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_replication': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_tagging': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_bucket_website': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'delete_object': {
        'required': ['Bucket', 'Key'],
        'optional': ['MFA', 'VersionId', 'RequestPayer', 'BypassGovernanceRetention', 'ExpectedBucketOwner']
    },
    'delete_object_tagging': {
        'required': ['Bucket', 'Key'],
        'optional': ['VersionId', 'ExpectedBucketOwner']
    },
    'delete_objects': {
        'required': ['Bucket', 'Delete'],
        'optional': ['MFA', 'RequestPayer', 'BypassGovernanceRetention', 'ExpectedBucketOwner', 'ChecksumAlgorithm']
    },
    'delete_public_access_block': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'download_file': {
        'required': ['Bucket', 'Key', 'Filename'],
        'optional': ['ExtraArgs', 'Callback', 'Config']
    },
    'download_fileobj': {
        'required': ['Bucket', 'Key', 'Fileobj'],
        'optional': ['ExtraArgs', 'Callback', 'Config']
    },
    'generate_presigned_post': {
        'required': ['Bucket', 'Key'],
        'optional': ['Fields', 'Conditions', 'ExpiresIn']
    },
    'generate_presigned_url': {
        'required': ['ClientMethod'],
        'optional': ['Params', 'ExpiresIn', 'HttpMethod']
    },
    'get_bucket_accelerate_configuration': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner', 'RequestPayer']
    },
    'get_bucket_acl': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_analytics_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_cors': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_encryption': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_intelligent_tiering_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_inventory_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_lifecycle': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_lifecycle_configuration': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_location': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_logging': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_metrics_configuration': {
        'required': ['Bucket', 'Id'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_notification': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_notification_configuration': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_ownership_controls': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_policy': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_policy_status': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_replication': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_request_payment': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_tagging': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_versioning': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_bucket_website': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_object': {
        'required': ['Bucket', 'Key'],
        'optional': ['IfMatch', 'IfModifiedSince', 'IfNoneMatch', 'IfUnmodifiedSince', 'Range', 'ResponseCacheControl', 'ResponseContentDisposition', 'ResponseContentEncoding', 'ResponseContentLanguage', 'ResponseContentType', 'ResponseExpires', 'VersionId', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'RequestPayer', 'PartNumber', 'ExpectedBucketOwner', 'ChecksumMode']
    },
    'get_object_acl': {
        'required': ['Bucket', 'Key'],
        'optional': ['VersionId', 'RequestPayer', 'ExpectedBucketOwner']
    },
    'get_object_attributes': {
        'required': ['Bucket', 'Key', 'ObjectAttributes'],
        'optional': ['VersionId', 'MaxParts', 'PartNumberMarker', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'RequestPayer', 'ExpectedBucketOwner']
    },
    'get_object_legal_hold': {
        'required': ['Bucket', 'Key'],
        'optional': ['VersionId', 'RequestPayer', 'ExpectedBucketOwner']
    },
    'get_object_lock_configuration': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'get_object_retention': {
        'required': ['Bucket', 'Key'],
        'optional': ['VersionId', 'RequestPayer', 'ExpectedBucketOwner']
    },
    'get_object_tagging': {
        'required': ['Bucket', 'Key'],
        'optional': ['VersionId', 'ExpectedBucketOwner', 'RequestPayer']
    },
    'get_object_torrent': {
        'required': ['Bucket', 'Key'],
        'optional': ['RequestPayer', 'ExpectedBucketOwner']
    },
    'get_public_access_block': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'head_bucket': {
        'required': ['Bucket'],
        'optional': ['ExpectedBucketOwner']
    },
    'head_object': {
        'required': ['Bucket', 'Key'],
        'optional': ['IfMatch', 'IfModifiedSince', 'IfNoneMatch', 'IfUnmodifiedSince', 'Range', 'VersionId', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'RequestPayer', 'PartNumber', 'ExpectedBucketOwner', 'ChecksumMode']
    },
    'list_bucket_analytics_configurations': {
        'required': ['Bucket'],
        'optional': ['ContinuationToken', 'ExpectedBucketOwner']
    },
    'list_bucket_intelligent_tiering_configurations': {
        'required': ['Bucket'],
        'optional': ['ContinuationToken', 'ExpectedBucketOwner']
    },
    'list_bucket_inventory_configurations': {
        'required': ['Bucket'],
        'optional': ['ContinuationToken', 'ExpectedBucketOwner']
    },
    'list_bucket_metrics_configurations': {
        'required': ['Bucket'],
        'optional': ['ContinuationToken', 'ExpectedBucketOwner']
    },
    'list_buckets': {
        'required': [],
        'optional': []
    },
    'list_multipart_uploads': {
        'required': ['Bucket'],
        'optional': ['Delimiter', 'EncodingType', 'KeyMarker', 'MaxUploads', 'Prefix', 'UploadIdMarker', 'ExpectedBucketOwner', 'RequestPayer']
    },
    'list_object_versions': {
        'required': ['Bucket'],
        'optional': ['Delimiter', 'EncodingType', 'KeyMarker', 'MaxKeys', 'Prefix', 'VersionIdMarker', 'ExpectedBucketOwner', 'RequestPayer', 'OptionalObjectAttributes']
    },
    'list_objects': {
        'required': ['Bucket'],
        'optional': ['Delimiter', 'EncodingType', 'Marker', 'MaxKeys', 'Prefix', 'RequestPayer', 'ExpectedBucketOwner', 'OptionalObjectAttributes']
    },
    'list_objects_v2': {
        'required': ['Bucket'],
        'optional': ['Delimiter', 'EncodingType', 'MaxKeys', 'Prefix', 'ContinuationToken', 'FetchOwner', 'StartAfter', 'RequestPayer', 'ExpectedBucketOwner', 'OptionalObjectAttributes']
    },
    'list_parts': {
        'required': ['Bucket', 'Key', 'UploadId'],
        'optional': ['MaxParts', 'PartNumberMarker', 'RequestPayer', 'ExpectedBucketOwner', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5']
    },
    'put_bucket_accelerate_configuration': {
        'required': ['Bucket', 'AccelerateConfiguration'],
        'optional': ['ExpectedBucketOwner', 'ChecksumAlgorithm']
    },
    'put_bucket_acl': {
        'required': ['Bucket'],
        'optional': ['ACL', 'AccessControlPolicy', 'ChecksumAlgorithm', 'GrantFullControl', 'GrantRead', 'GrantReadACP', 'GrantWrite', 'GrantWriteACP', 'ExpectedBucketOwner']
    },
    'put_bucket_analytics_configuration': {
        'required': ['Bucket', 'Id', 'AnalyticsConfiguration'],
        'optional': ['ExpectedBucketOwner']
    },
    'put_bucket_cors': {
        'required': ['Bucket', 'CORSConfiguration'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_encryption': {
        'required': ['Bucket', 'ServerSideEncryptionConfiguration'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_intelligent_tiering_configuration': {
        'required': ['Bucket', 'Id', 'IntelligentTieringConfiguration'],
        'optional': ['ExpectedBucketOwner']
    },
    'put_bucket_inventory_configuration': {
        'required': ['Bucket', 'Id', 'InventoryConfiguration'],
        'optional': ['ExpectedBucketOwner']
    },
    'put_bucket_lifecycle': {
        'required': ['Bucket', 'LifecycleConfiguration'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_lifecycle_configuration': {
        'required': ['Bucket'],
        'optional': ['ChecksumAlgorithm', 'LifecycleConfiguration', 'ExpectedBucketOwner']
    },
    'put_bucket_logging': {
        'required': ['Bucket', 'BucketLoggingStatus'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_metrics_configuration': {
        'required': ['Bucket', 'Id', 'MetricsConfiguration'],
        'optional': ['ExpectedBucketOwner']
    },
    'put_bucket_notification': {
        'required': ['Bucket', 'NotificationConfiguration'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_notification_configuration': {
        'required': ['Bucket', 'NotificationConfiguration'],
        'optional': ['ExpectedBucketOwner', 'SkipDestinationValidation']
    },
    'put_bucket_ownership_controls': {
        'required': ['Bucket', 'OwnershipControls'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_policy': {
        'required': ['Bucket', 'Policy'],
        'optional': ['ChecksumAlgorithm', 'ConfirmRemoveSelfBucketAccess', 'ExpectedBucketOwner']
    },
    'put_bucket_replication': {
        'required': ['Bucket', 'ReplicationConfiguration'],
        'optional': ['ChecksumAlgorithm', 'Token', 'ExpectedBucketOwner']
    },
    'put_bucket_request_payment': {
        'required': ['Bucket', 'RequestPaymentConfiguration'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_tagging': {
        'required': ['Bucket', 'Tagging'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_bucket_versioning': {
        'required': ['Bucket', 'VersioningConfiguration'],
        'optional': ['ChecksumAlgorithm', 'MFA', 'ExpectedBucketOwner']
    },
    'put_bucket_website': {
        'required': ['Bucket', 'WebsiteConfiguration'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_object': {
        'required': ['Bucket', 'Key'],
        'optional': ['ACL', 'Body', 'CacheControl', 'ContentDisposition', 'ContentEncoding', 'ContentLanguage', 'ContentLength', 'ContentMD5', 'ContentType', 'ChecksumAlgorithm', 'ChecksumCRC32', 'ChecksumCRC32C', 'ChecksumSHA1', 'ChecksumSHA256', 'Expires', 'GrantFullControl', 'GrantRead', 'GrantReadACP', 'GrantWriteACP', 'Metadata', 'ServerSideEncryption', 'StorageClass', 'WebsiteRedirectLocation', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'SSEKMSKeyId', 'SSEKMSEncryptionContext', 'BucketKeyEnabled', 'RequestPayer', 'Tagging', 'ObjectLockMode', 'ObjectLockRetainUntilDate', 'ObjectLockLegalHoldStatus', 'ExpectedBucketOwner']
    },
    'put_object_acl': {
        'required': ['Bucket', 'Key'],
        'optional': ['ACL', 'AccessControlPolicy', 'ChecksumAlgorithm', 'GrantFullControl', 'GrantRead', 'GrantReadACP', 'GrantWrite', 'GrantWriteACP', 'RequestPayer', 'VersionId', 'ExpectedBucketOwner']
    },
    'put_object_legal_hold': {
        'required': ['Bucket', 'Key'],
        'optional': ['LegalHold', 'RequestPayer', 'VersionId', 'ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_object_lock_configuration': {
        'required': ['Bucket'],
        'optional': ['ObjectLockConfiguration', 'RequestPayer', 'Token', 'ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_object_retention': {
        'required': ['Bucket', 'Key'],
        'optional': ['Retention', 'RequestPayer', 'VersionId', 'BypassGovernanceRetention', 'ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'put_object_tagging': {
        'required': ['Bucket', 'Key', 'Tagging'],
        'optional': ['VersionId', 'ChecksumAlgorithm', 'ExpectedBucketOwner', 'RequestPayer']
    },
    'put_public_access_block': {
        'required': ['Bucket', 'PublicAccessBlockConfiguration'],
        'optional': ['ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'restore_object': {
        'required': ['Bucket', 'Key'],
        'optional': ['VersionId', 'RestoreRequest', 'RequestPayer', 'ChecksumAlgorithm', 'ExpectedBucketOwner']
    },
    'select_object_content': {
        'required': ['Bucket', 'Key', 'Expression', 'ExpressionType', 'InputSerialization', 'OutputSerialization'],
        'optional': ['RequestProgress', 'ScanRange', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'ExpectedBucketOwner']
    },
    'upload_file': {
        'required': ['Filename', 'Bucket', 'Key'],
        'optional': ['ExtraArgs', 'Callback', 'Config']
    },
    'upload_fileobj': {
        'required': ['Fileobj', 'Bucket', 'Key'],
        'optional': ['ExtraArgs', 'Callback', 'Config']
    },
    'upload_part': {
        'required': ['Bucket', 'Key', 'PartNumber', 'UploadId'],
        'optional': ['Body', 'ContentLength', 'ContentMD5', 'ChecksumAlgorithm', 'ChecksumCRC32', 'ChecksumCRC32C', 'ChecksumSHA1', 'ChecksumSHA256', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'RequestPayer', 'ExpectedBucketOwner']
    },
    'upload_part_copy': {
        'required': ['Bucket', 'CopySource', 'Key', 'PartNumber', 'UploadId'],
        'optional': ['CopySourceIfMatch', 'CopySourceIfModifiedSince', 'CopySourceIfNoneMatch', 'CopySourceIfUnmodifiedSince', 'CopySourceRange', 'SSECustomerAlgorithm', 'SSECustomerKey', 'SSECustomerKeyMD5', 'CopySourceSSECustomerAlgorithm', 'CopySourceSSECustomerKey', 'CopySourceSSECustomerKeyMD5', 'RequestPayer', 'ExpectedBucketOwner', 'ExpectedSourceBucketOwner']
    }
}

# Parameter type mappings
PARAMETER_TYPES = {
    'Bucket': 'str',
    'Key': 'str',
    'Body': 'Any',
    'ACL': 'str',
    'CacheControl': 'str',
    'ContentDisposition': 'str',
    'ContentEncoding': 'str',
    'ContentLanguage': 'str',
    'ContentLength': 'int',
    'ContentMD5': 'str',
    'ContentType': 'str',
    'Expires': 'datetime',
    'GrantFullControl': 'str',
    'GrantRead': 'str',
    'GrantReadACP': 'str',
    'GrantWrite': 'str',
    'GrantWriteACP': 'str',
    'Metadata': 'Dict[str, str]',
    'ServerSideEncryption': 'str',
    'StorageClass': 'str',
    'WebsiteRedirectLocation': 'str',
    'SSECustomerAlgorithm': 'str',
    'SSECustomerKey': 'str',
    'SSECustomerKeyMD5': 'str',
    'SSEKMSKeyId': 'str',
    'SSEKMSEncryptionContext': 'str',
    'BucketKeyEnabled': 'bool',
    'RequestPayer': 'str',
    'Tagging': 'str',
    'ObjectLockMode': 'str',
    'ObjectLockRetainUntilDate': 'datetime',
    'ObjectLockLegalHoldStatus': 'str',
    'ExpectedBucketOwner': 'str',
    'ChecksumAlgorithm': 'str',
    'ChecksumCRC32': 'str',
    'ChecksumCRC32C': 'str',
    'ChecksumSHA1': 'str',
    'ChecksumSHA256': 'str',
    'VersionId': 'str',
    'MFA': 'str',
    'BypassGovernanceRetention': 'bool',
    'UploadId': 'str',
    'PartNumber': 'int',
    'MultipartUpload': 'Dict[str, Any]',
    'CopySource': 'Union[str, Dict[str, Any]]',
    'CopySourceIfMatch': 'str',
    'CopySourceIfModifiedSince': 'datetime',
    'CopySourceIfNoneMatch': 'str',
    'CopySourceIfUnmodifiedSince': 'datetime',
    'MetadataDirective': 'str',
    'TaggingDirective': 'str',
    'CopySourceSSECustomerAlgorithm': 'str',
    'CopySourceSSECustomerKey': 'str',
    'CopySourceSSECustomerKeyMD5': 'str',
    'ExpectedSourceBucketOwner': 'str',
    'CreateBucketConfiguration': 'Dict[str, Any]',
    'ObjectLockEnabledForBucket': 'bool',
    'ObjectOwnership': 'str',
    'Id': 'str',
    'Delete': 'Dict[str, Any]',
    'Filename': 'str',
    'Fileobj': 'Any',
    'ExtraArgs': 'Optional[Dict[str, Any]]',
    'Callback': 'Optional[Any]',
    'Config': 'Optional[Any]',
    'Fields': 'Optional[Dict[str, Any]]',
    'Conditions': 'Optional[List[Any]]',
    'ExpiresIn': 'int',
    'ClientMethod': 'str',
    'Params': 'Optional[Dict[str, Any]]',
    'HttpMethod': 'str',
    'IfMatch': 'str',
    'IfModifiedSince': 'datetime',
    'IfNoneMatch': 'str',
    'IfUnmodifiedSince': 'datetime',
    'Range': 'str',
    'ResponseCacheControl': 'str',
    'ResponseContentDisposition': 'str',
    'ResponseContentEncoding': 'str',
    'ResponseContentLanguage': 'str',
    'ResponseContentType': 'str',
    'ResponseExpires': 'datetime',
    'ChecksumMode': 'str',
    'ObjectAttributes': 'List[str]',
    'MaxParts': 'int',
    'PartNumberMarker': 'int',
    'Delimiter': 'str',
    'EncodingType': 'str',
    'KeyMarker': 'str',
    'MaxUploads': 'int',
    'Prefix': 'str',
    'UploadIdMarker': 'str',
    'VersionIdMarker': 'str',
    'MaxKeys': 'int',
    'OptionalObjectAttributes': 'List[str]',
    'Marker': 'str',
    'ContinuationToken': 'str',
    'FetchOwner': 'bool',
    'StartAfter': 'str',
    'PartNumberMarker': 'int',
    'AccelerateConfiguration': 'Dict[str, Any]',
    'AccessControlPolicy': 'Dict[str, Any]',
    'AnalyticsConfiguration': 'Dict[str, Any]',
    'CORSConfiguration': 'Dict[str, Any]',
    'ServerSideEncryptionConfiguration': 'Dict[str, Any]',
    'IntelligentTieringConfiguration': 'Dict[str, Any]',
    'InventoryConfiguration': 'Dict[str, Any]',
    'LifecycleConfiguration': 'Dict[str, Any]',
    'BucketLoggingStatus': 'Dict[str, Any]',
    'MetricsConfiguration': 'Dict[str, Any]',
    'NotificationConfiguration': 'Dict[str, Any]',
    'OwnershipControls': 'Dict[str, Any]',
    'Policy': 'str',
    'ConfirmRemoveSelfBucketAccess': 'bool',
    'ReplicationConfiguration': 'Dict[str, Any]',
    'Token': 'str',
    'RequestPaymentConfiguration': 'Dict[str, Any]',
    'VersioningConfiguration': 'Dict[str, Any]',
    'WebsiteConfiguration': 'Dict[str, Any]',
    'LegalHold': 'Dict[str, Any]',
    'ObjectLockConfiguration': 'Dict[str, Any]',
    'Retention': 'Dict[str, Any]',
    'PublicAccessBlockConfiguration': 'Dict[str, Any]',
    'RestoreRequest': 'Dict[str, Any]',
    'Expression': 'str',
    'ExpressionType': 'str',
    'InputSerialization': 'Dict[str, Any]',
    'OutputSerialization': 'Dict[str, Any]',
    'RequestProgress': 'Dict[str, Any]',
    'ScanRange': 'Dict[str, Any]',
    'CopySourceRange': 'str',
    'SkipDestinationValidation': 'bool'
}


def generate_method_signature(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate proper method signature with explicit parameters on separate lines."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    # Build parameter list with each parameter on separate line
    params = []
    
    # Add required parameters
    for param in required_params:
        param_type = PARAMETER_TYPES.get(param, 'Any')
        params.append(f"        {param}: {param_type}")
    
    # Add optional parameters
    for param in optional_params:
        param_type = PARAMETER_TYPES.get(param, 'Any')
        if not param_type.startswith('Optional'):
            param_type = f"Optional[{param_type}]"
        params.append(f"        {param}: {param_type} = None")
    
    if params:
        params_str = ",\n" + ",\n".join(params)
    else:
        params_str = ""
    
    return f"async def {method_name}(self{params_str}) -> S3Response:"


def generate_method_docstring(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate comprehensive docstring."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    # Create description
    description = f"S3 {method_name.replace('_', ' ').title()} operation."
    
    docstring = f'        """{description}\n\n'
    
    # Add parameters
    all_params = required_params + optional_params
    if all_params:
        docstring += '        Args:\n'
        for param in required_params:
            param_type = PARAMETER_TYPES.get(param, 'Any')
            docstring += f'            {param} ({param_type}): Required parameter\n'
        for param in optional_params:
            param_type = PARAMETER_TYPES.get(param, 'Any')
            docstring += f'            {param} (Optional[{param_type}]): Optional parameter\n'
    
    docstring += '\n        Returns:\n'
    docstring += '            S3Response: Standardized response with success/data/error format\n'
    docstring += '        """'
    
    return docstring


def generate_method_body(method_name: str, method_def: Dict[str, List[str]]) -> str:
    """Generate method body with proper parameter handling using aioboto3."""
    required_params = method_def.get('required', [])
    optional_params = method_def.get('optional', [])
    
    # Build kwargs dictionary
    if not required_params and not optional_params:
        kwargs_setup = "        kwargs = {}"
    else:
        kwargs_lines = []
        
        # Add required parameters
        if required_params:
            req_params_str = ", ".join([f"'{p}': {p}" for p in required_params])
            kwargs_lines.append(f"        kwargs = {{{req_params_str}}}")
        else:
            kwargs_lines.append("        kwargs = {}")
        
        # Add optional parameters
        for param in optional_params:
            kwargs_lines.append(f"        if {param} is not None:")
            kwargs_lines.append(f"            kwargs['{param}'] = {param}")
        
        kwargs_setup = "\n".join(kwargs_lines)
    
    return f'''{kwargs_setup}
        
        try:
            session = await self._get_aioboto3_session()
            async with session.client('s3') as s3_client:
                response = await getattr(s3_client, '{method_name}')(**kwargs)
                return self._handle_s3_response(response)
        except ClientError as e:
            error_code = e.response.get('Error', {{}}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {{}}).get('Message', str(e))
            return S3Response(success=False, error=f"{{error_code}}: {{error_message}}")
        except Exception as e:
            return S3Response(success=False, error=f"Unexpected error: {{str(e)}}")'''


def generate_complete_s3_data_source() -> str:
    """Generate complete S3DataSource with proper explicit signatures."""
    
    class_code = f'''from typing import Dict, List, Optional, Union, Any, BinaryIO
import asyncio
import json
from dataclasses import asdict
from datetime import datetime

try:
    import aioboto3  # type: ignore
    from botocore.exceptions import ClientError, BotoCoreError  # type: ignore
except ImportError:
    raise ImportError("aioboto3 is not installed. Please install it with `pip install aioboto3`")

from app.sources.client.s3.s3 import S3Client, S3Response


class S3DataSource:
    """
    Complete Amazon S3 API client wrapper with EXPLICIT METHOD SIGNATURES using aioboto3.
    
    All {len(S3_METHOD_SIGNATURES)} S3 methods with proper parameter signatures:
    - Required parameters are explicitly typed (e.g., Bucket: str, Key: str)
    - Optional parameters use Optional[Type] = None
    - No **kwargs - every parameter is explicitly defined
    - Matches aioboto3 S3 client signatures exactly
    - Each parameter on separate line for better readability
    """
    
    def __init__(self, s3_client: S3Client) -> None:
        """Initialize with S3Client."""
        self._s3_client = s3_client
        self._session = None

    async def _get_aioboto3_session(self):
        """Get or create the aioboto3 session."""
        if self._session is None:
            # Option 1: Get the existing session directly from S3Client (recommended)
            self._session = self._s3_client.get_session()
            
            # Option 2: Create new session from credentials (if needed)
            # credentials = self._s3_client.get_credentials()
            # self._session = aioboto3.Session(
            #     aws_access_key_id=credentials['aws_access_key_id'],
            #     aws_secret_access_key=credentials['aws_secret_access_key'],
            #     region_name=credentials['region_name']
            # )
        return self._session

    def _handle_s3_response(self, response: Any) -> S3Response:
        """Handle S3 API response with comprehensive error handling."""
        try:
            if response is None:
                return S3Response(success=False, error="Empty response from S3 API")
            
            if isinstance(response, dict):
                if 'Error' in response:
                    error_info = response['Error']
                    error_code = error_info.get('Code', 'Unknown')
                    error_message = error_info.get('Message', 'No message')
                    return S3Response(success=False, error=f"{{error_code}}: {{error_message}}")
                return S3Response(success=True, data=response)
            
            return S3Response(success=True, data=response)
            
        except Exception as e:
            return S3Response(success=False, error=f"Response handling error: {{str(e)}}")

'''

    # Generate all methods with proper signatures
    for method_name, method_def in sorted(S3_METHOD_SIGNATURES.items()):
        try:
            signature = generate_method_signature(method_name, method_def)
            docstring = generate_method_docstring(method_name, method_def)
            method_body = generate_method_body(method_name, method_def)
            
            complete_method = f"    {signature}\n{docstring}\n{method_body}\n\n"
            class_code += complete_method
            
        except Exception as e:
            print(f"Warning: Failed to generate method {method_name}: {e}")
    
    # Add utility methods
    class_code += '''    # Utility Methods
    async def get_aioboto3_session(self):
        """Get the underlying aioboto3 session."""
        return await self._get_aioboto3_session()
    
    def get_s3_client(self) -> S3Client:
        """Get the S3Client wrapper."""
        return self._s3_client
    
    async def get_sdk_info(self) -> S3Response:
        """Get information about the wrapped SDK methods."""
        info = {
            'total_methods': ''' + str(len(S3_METHOD_SIGNATURES)) + ''',
            'sdk_version': aioboto3.__version__,
            'service': 's3'
        }
        return S3Response(success=True, data=info)
'''
    
    return class_code


def main():
    """Generate and save the complete S3DataSource with explicit signatures."""
    print(f"🚀 Generating COMPLETE S3DataSource with EXPLICIT PARAMETER SIGNATURES...")
    
    try:
        # Generate the complete class
        class_code = generate_complete_s3_data_source()
        
        # Create s3 directory
        script_dir = Path(__file__).parent if __file__ else Path('.')
        s3_dir = script_dir / 's3'
        s3_dir.mkdir(exist_ok=True)
        
        # Save to file
        output_file = s3_dir / 's3_data_source.py'
        output_file.write_text(class_code, encoding='utf-8')
        
        method_count = len(S3_METHOD_SIGNATURES)
        print(f"✅ Generated COMPLETE S3DataSource with {method_count} S3 methods!")
        print(f"📁 Saved to: {output_file}")
        print(f"\n🎯 ALL METHODS HAVE EXPLICIT SIGNATURES:")
        print(f"   ✅ {method_count} methods with proper parameter signatures")
        print(f"   ✅ Required parameters explicitly typed (Bucket: str, Key: str)")
        print(f"   ✅ Optional parameters with Optional[Type] = None")
        print(f"   ✅ No **kwargs - every parameter explicitly defined")
        print(f"   ✅ Matches boto3 S3 client signatures exactly")
        print(f"\n📝 Example Usage:")
        print(f"   # All parameters are explicit and typed")
        print(f"   await s3_ds.put_object(")
        print(f"       Bucket='my-bucket',")
        print(f"       Key='file.txt',")
        print(f"       Body=b'data',")
        print(f"       ContentType='text/plain',")
        print(f"       ServerSideEncryption='AES256'")
        print(f"   )")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()