"""Configuration for S3 connector"""

from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.enums.enums import AuthenticationType, ConnectorType
from app.connectors.sources.s3.const.const import (
    AWS_S3_API_VERSION,
    AWS_S3_BASE_URL,
    S3_SCOPE_GET_BUCKET_LOCATION,
    S3_SCOPE_GET_OBJECT,
    S3_SCOPE_LIST_BUCKET,
)

# S3 connector configuration
S3_CONFIG = ConnectorConfig(
    connector_type=ConnectorType.S3,
    authentication_type=AuthenticationType.API_KEY,  # AWS uses access key/secret key
    base_url=AWS_S3_BASE_URL,
    api_version=AWS_S3_API_VERSION,  # S3 API version
    rate_limits={
        "max_rate": 3500,  # S3 has very high rate limits
        "time_window": 1
    },
    scopes=[S3_SCOPE_LIST_BUCKET, S3_SCOPE_GET_OBJECT, S3_SCOPE_GET_BUCKET_LOCATION],
    webhook_support=False,  # S3 doesn't have webhooks, uses SNS/SQS for notifications
    batch_operations=True,
    real_time_sync=False  # S3 is not real-time, it's object storage
)
