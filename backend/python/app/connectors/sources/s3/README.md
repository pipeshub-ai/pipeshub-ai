# S3 Connector

## Overview

The S3 connector allows you to integrate with AWS S3 to access and manage buckets, objects, and other S3 resources using the PipesHub connector framework with **aioboto3** for async AWS operations.

## Features

- ✅ Authentication with AWS Access Key/Secret Key using aioboto3
- ✅ List S3 buckets with async operations
- ✅ List objects in buckets with pagination using aioboto3
- ✅ Get bucket metadata (region, versioning, encryption, etc.)
- ✅ Get object content with async streaming
- ✅ Search objects by prefix using aioboto3
- ✅ Support for all S3 storage classes
- ✅ Error handling and logging
- ✅ Rate limiting support
- ✅ AWS STS integration for identity verification
- ✅ Full async/await support with aioboto3

## Configuration

```python
from app.connectors.sources.s3.config import S3_CONFIG

# Configuration includes:
# - API version: 2006-03-01
# - Rate limits: 3500 req/sec, 5500 req/min
# - Authentication: API Key (AWS Access Key/Secret Key)
# - Webhook support: No (uses SNS/SQS for notifications)
# - Batch operations: Yes
# - Real-time sync: No (object storage)
```

## Authentication

The connector uses AWS credentials for authentication with **aioboto3**:

1. **Access Key/Secret Key**: Standard AWS credentials
2. **Session Token**: For temporary credentials (optional)
3. **Region**: AWS region for S3 operations
4. **IAM Roles**: Can be used instead of access keys
5. **Async Session**: Uses aioboto3.Session for async operations

### Required Permissions

The AWS credentials need the following S3 permissions:
- `s3:ListBucket` - List buckets and objects
- `s3:GetObject` - Read object content
- `s3:GetBucketLocation` - Get bucket region
- `s3:GetBucketVersioning` - Get versioning status
- `s3:GetBucketEncryption` - Get encryption settings
- `s3:GetBucketPolicy` - Get bucket policy

## Usage

### Basic Usage

```python
from app.connectors.sources.s3.factories.connector_factory import S3ConnectorFactory

# Create connector using aioboto3
connector = S3ConnectorFactory.create_connector(logger)

# Connect with aioboto3
credentials = {
    "aws_access_key_id": "your_access_key",
    "aws_secret_access_key": "your_secret_key",
    "region_name": "us-east-1"
}
await connector.connect(credentials)

# List buckets using aioboto3
buckets = await connector.list_buckets()

# List objects in a bucket using aioboto3
objects = await connector.list_bucket_objects("my-bucket", max_keys=100)

# Get bucket metadata using aioboto3
metadata = await connector.get_bucket_metadata("my-bucket")

# Get object content using aioboto3
content = await connector.get_object_content("my-bucket", "path/to/file.txt")
```

### Advanced Usage

```python
# Search objects by prefix using aioboto3
results = await connector.search_objects("important", "my-bucket")

# Get service information
info = connector.get_service_info()
print(f"AWS Account ID: {info['aws_account_id']}")
print(f"AWS Region: {info['aws_region']}")
print(f"Supported operations: {info['supported_operations']}")

# Test connection using aioboto3
if await connector.test_connection():
    print("✅ Connection successful using aioboto3")
```

## API Reference

### S3ConnectorService

#### Methods

- `async connect(credentials: Dict[str, Any]) -> bool`: Connect to AWS S3
- `async disconnect() -> bool`: Disconnect from AWS S3
- `async test_connection() -> bool`: Test the connection
- `async list_buckets() -> List[Dict[str, Any]]`: List all S3 buckets
- `async list_bucket_objects(bucket_name: str, prefix: str = '', max_keys: int = 1000) -> List[Dict[str, Any]]`: List objects in a bucket
- `async get_bucket_metadata(bucket_name: str) -> Dict[str, Any]`: Get bucket metadata
- `async get_object_content(bucket_name: str, object_key: str) -> Optional[bytes]`: Get object content
- `async search_objects(query: str, bucket_name: str = None) -> List[Dict[str, Any]]`: Search objects by prefix

### S3AuthenticationService

#### Methods

- `async authenticate(credentials: Dict[str, Any]) -> bool`: Authenticate with AWS
- `async validate_token(token: str) -> bool`: Validate AWS credentials
- `async refresh_token(refresh_token: str) -> Dict[str, Any]`: Refresh temporary credentials
- `async revoke_token(token: str) -> bool`: Clear credentials
- `async disconnect() -> bool`: Disconnect and cleanup
- `get_account_id() -> Optional[str]`: Get AWS account ID
- `get_region_name() -> Optional[str]`: Get AWS region
- `get_credentials_dict() -> Dict[str, str]`: Get credentials as dictionary

### S3DataService

#### Methods

- `async list_items(path: str = "/", recursive: bool = True) -> List[Dict[str, Any]]`: List S3 buckets
- `async get_item_metadata(item_id: str) -> Optional[Dict[str, Any]]`: Get bucket metadata
- `async get_item_content(item_id: str) -> Optional[bytes]`: Get object content (bucket:key format)
- `async search_items(query: str, filters: Dict[str, Any] = None) -> List[Dict[str, Any]]`: Search objects
- `async get_item_permissions(item_id: str) -> List[Dict[str, Any]]`: Get permissions (not fully implemented)
- `async list_bucket_objects(bucket_name: str, prefix: str = '', max_keys: int = 1000) -> List[Dict[str, Any]]`: List objects in bucket

## Data Structures

### S3Object
```python
@dataclass
class S3Object:
    key: str
    size: int
    last_modified: str
    etag: str
    storage_class: str
    bucket_name: str
```

### S3Bucket
```python
@dataclass
class S3Bucket:
    name: str
    creation_date: str
    region: str
```

## Error Handling

The connector includes comprehensive error handling for:

- **Authentication failures**: Invalid credentials, expired tokens
- **Permission errors**: Access denied to buckets/objects
- **Network errors**: Connection timeouts, DNS failures
- **AWS API errors**: Rate limiting, service unavailable
- **Invalid requests**: Non-existent buckets/objects

## Rate Limits

AWS S3 has very high rate limits:
- 3,500 requests per second
- 5,500 requests per minute
- 1,000,000 requests per day

The connector respects these limits and includes retry logic for rate limit errors.

## Storage Classes

The connector supports all S3 storage classes:
- **STANDARD**: Standard storage
- **STANDARD_IA**: Standard Infrequent Access
- **ONEZONE_IA**: One Zone Infrequent Access
- **INTELLIGENT_TIERING**: Intelligent Tiering
- **GLACIER**: Glacier storage
- **DEEP_ARCHIVE**: Deep Archive storage

## Integration with Framework

The S3 connector follows the PipesHub connector framework patterns with **aioboto3**:

1. **Authentication Service**: Handles AWS credentials and aioboto3 session management
2. **Data Service**: Manages S3 operations (buckets, objects, metadata) using aioboto3
3. **Connector Service**: Main interface for the application with async operations
4. **Factory**: Creates and registers the connector
5. **Configuration**: Defines connector settings and limits
6. **Async Support**: Full async/await support with aioboto3

## Testing

Run the example usage:

```bash
cd backend/python/app/connectors/sources/s3
python example_usage.py
```

## Dependencies

- `aioboto3`: Async AWS SDK for Python (primary dependency)
- `botocore`: AWS SDK core functionality
- `asyncio`: Async/await support
- `logging`: Logging functionality
- `aiohttp`: HTTP client for aioboto3

## Contributing

1. Follow the existing code patterns
2. Add tests for new functionality
3. Update documentation
4. Ensure error handling is comprehensive
5. Respect AWS rate limits and best practices

## Security Considerations

- Never hardcode AWS credentials in code
- Use IAM roles when possible
- Implement least privilege access
- Rotate access keys regularly
- Monitor S3 access logs
- Use encryption for sensitive data

## Troubleshooting

### Common Issues

1. **Invalid credentials**: Check AWS access key and secret key
2. **Access denied**: Verify IAM permissions for S3
3. **Region mismatch**: Ensure region matches bucket location
4. **Rate limiting**: Implement exponential backoff
5. **Network issues**: Check internet connectivity and AWS endpoints

### Debug Mode

Enable debug logging to see detailed AWS API calls:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Examples

See `example_usage.py` for complete working examples including:
- Basic bucket and object operations
- Advanced batch processing
- Error handling patterns
- Integration with main application 