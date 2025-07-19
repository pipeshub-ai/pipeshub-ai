"""Constants for S3 connector"""

# AWS Credential Keys
AWS_ACCESS_KEY_ID = "aws_access_key_id"
AWS_SECRET_ACCESS_KEY = "aws_secret_access_key"
REGION_NAME = "region_name"

# AWS Session Keys
AWS_SESSION_TOKEN = "aws_session_token"

# Default Regions
US_EAST_REGION = "us-east-1"
AP_SOUTH_REGION = "ap-south-1"

# AWS S3 Scopes
S3_SCOPE_LIST_BUCKET = "s3:ListBucket"
S3_SCOPE_GET_OBJECT = "s3:GetObject"
S3_SCOPE_GET_BUCKET_LOCATION = "s3:GetBucketLocation"

# AWS S3 API Version
AWS_S3_API_VERSION = "xxx"

# AWS S3 Base URL
AWS_S3_BASE_URL = "xxx"

# Batch Processing
DEFAULT_BATCH_SIZE = 10
DEFAULT_MAX_KEYS = 1000
LARGE_FILE_THRESHOLD = 1024 * 1024  # 1MB

# Rate Limits
DEFAULT_REQUESTS_PER_SECOND = 3500
DEFAULT_REQUESTS_PER_MINUTE = 5500
DEFAULT_REQUESTS_PER_DAY = 1000000

# Supported Operations
OPERATION_LIST_BUCKETS = "list_buckets"
OPERATION_LIST_BUCKET_OBJECTS = "list_bucket_objects"
OPERATION_GET_BUCKET_METADATA = "get_bucket_metadata"
OPERATION_GET_OBJECT_CONTENT = "get_object_content"
OPERATION_SEARCH_OBJECTS = "search_objects"
