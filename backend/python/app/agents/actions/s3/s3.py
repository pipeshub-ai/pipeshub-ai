import asyncio
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.s3.s3 import S3Client
from app.sources.external.s3.s3 import S3DataSource

logger = logging.getLogger(__name__)


class S3:
    """S3 tool exposed to the agents"""
    def __init__(self, client: S3Client) -> None:
        """Initialize the S3 tool"""
        """
        Args:
            client: S3 client object
        Returns:
            None
        """
        self.client = S3DataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

    @tool(
        app_name="s3",
        tool_name="list_buckets",
        description="List S3 buckets",
        parameters=[]
    )
    def list_buckets(self) -> Tuple[bool, str]:
        """List S3 buckets"""
        """
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            response = self._run_async(self.client.list_buckets())

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing buckets: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="s3",
        tool_name="create_bucket",
        description="Create an S3 bucket",
        parameters=[
            ToolParameter(
                name="bucket_name",
                type=ParameterType.STRING,
                description="Name of the bucket to create",
                required=True
            ),
            ToolParameter(
                name="region",
                type=ParameterType.STRING,
                description="AWS region for the bucket",
                required=False
            )
        ]
    )
    def create_bucket(
        self,
        bucket_name: str,
        region: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Create an S3 bucket"""
        """
        Args:
            bucket_name: Name of the bucket to create
            region: AWS region for the bucket
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            response = self._run_async(self.client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={'LocationConstraint': region} if region else None
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error creating bucket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="s3",
        tool_name="delete_bucket",
        description="Delete an S3 bucket",
        parameters=[
            ToolParameter(
                name="bucket_name",
                type=ParameterType.STRING,
                description="Name of the bucket to delete",
                required=True
            )
        ]
    )
    def delete_bucket(self, bucket_name: str) -> Tuple[bool, str]:
        """Delete an S3 bucket"""
        """
        Args:
            bucket_name: Name of the bucket to delete
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            response = self._run_async(self.client.delete_bucket(Bucket=bucket_name))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting bucket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="s3",
        tool_name="list_objects",
        description="List objects in an S3 bucket",
        parameters=[
            ToolParameter(
                name="bucket_name",
                type=ParameterType.STRING,
                description="Name of the bucket",
                required=True
            ),
            ToolParameter(
                name="prefix",
                type=ParameterType.STRING,
                description="Prefix to filter objects",
                required=False
            ),
            ToolParameter(
                name="max_keys",
                type=ParameterType.INTEGER,
                description="Maximum number of objects to return",
                required=False
            ),
            ToolParameter(
                name="marker",
                type=ParameterType.STRING,
                description="Marker for pagination",
                required=False
            )
        ]
    )
    def list_objects(
        self,
        bucket_name: str,
        prefix: Optional[str] = None,
        max_keys: Optional[int] = None,
        marker: Optional[str] = None
    ) -> Tuple[bool, str]:
        """List objects in an S3 bucket"""
        """
        Args:
            bucket_name: Name of the bucket
            prefix: Prefix to filter objects
            max_keys: Maximum number of objects to return
            marker: Marker for pagination
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            response = self._run_async(self.client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=prefix,
                MaxKeys=max_keys,
                ContinuationToken=marker
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing objects: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="s3",
        tool_name="get_object",
        description="Get an object from S3",
        parameters=[
            ToolParameter(
                name="bucket_name",
                type=ParameterType.STRING,
                description="Name of the bucket",
                required=True
            ),
            ToolParameter(
                name="key",
                type=ParameterType.STRING,
                description="Key of the object",
                required=True
            )
        ]
    )
    def get_object(
        self,
        bucket_name: str,
        key: str
    ) -> Tuple[bool, str]:
        """Get an object from S3"""
        """
        Args:
            bucket_name: Name of the bucket
            key: Key of the object
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            response = self._run_async(self.client.get_object(
                Bucket=bucket_name,
                Key=key
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting object: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="s3",
        tool_name="put_object",
        description="Upload an object to S3",
        parameters=[
            ToolParameter(
                name="bucket_name",
                type=ParameterType.STRING,
                description="Name of the bucket",
                required=True
            ),
            ToolParameter(
                name="key",
                type=ParameterType.STRING,
                description="Key of the object",
                required=True
            ),
            ToolParameter(
                name="body",
                type=ParameterType.STRING,
                description="Content of the object",
                required=True
            ),
            ToolParameter(
                name="content_type",
                type=ParameterType.STRING,
                description="Content type of the object",
                required=False
            )
        ]
    )
    def put_object(
        self,
        bucket_name: str,
        key: str,
        body: str,
        content_type: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Upload an object to S3"""
        """
        Args:
            bucket_name: Name of the bucket
            key: Key of the object
            body: Content of the object
            content_type: Content type of the object
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            extra_args = {}
            if content_type:
                extra_args['ContentType'] = content_type

            response = self._run_async(self.client.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=body,
                **extra_args
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error putting object: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="s3",
        tool_name="delete_object",
        description="Delete an object from S3",
        parameters=[
            ToolParameter(
                name="bucket_name",
                type=ParameterType.STRING,
                description="Name of the bucket",
                required=True
            ),
            ToolParameter(
                name="key",
                type=ParameterType.STRING,
                description="Key of the object",
                required=True
            )
        ]
    )
    def delete_object(
        self,
        bucket_name: str,
        key: str
    ) -> Tuple[bool, str]:
        """Delete an object from S3"""
        """
        Args:
            bucket_name: Name of the bucket
            key: Key of the object
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            response = self._run_async(self.client.delete_object(
                Bucket=bucket_name,
                Key=key
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting object: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="s3",
        tool_name="copy_object",
        description="Copy an object in S3",
        parameters=[
            ToolParameter(
                name="source_bucket",
                type=ParameterType.STRING,
                description="Name of the source bucket",
                required=True
            ),
            ToolParameter(
                name="source_key",
                type=ParameterType.STRING,
                description="Key of the source object",
                required=True
            ),
            ToolParameter(
                name="dest_bucket",
                type=ParameterType.STRING,
                description="Name of the destination bucket",
                required=True
            ),
            ToolParameter(
                name="dest_key",
                type=ParameterType.STRING,
                description="Key of the destination object",
                required=True
            )
        ]
    )
    def copy_object(
        self,
        source_bucket: str,
        source_key: str,
        dest_bucket: str,
        dest_key: str
    ) -> Tuple[bool, str]:
        """Copy an object in S3"""
        """
        Args:
            source_bucket: Name of the source bucket
            source_key: Key of the source object
            dest_bucket: Name of the destination bucket
            dest_key: Key of the destination object
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use S3DataSource method
            response = self._run_async(self.client.copy_object(
                Bucket=dest_bucket,
                Key=dest_key,
                CopySource={'Bucket': source_bucket, 'Key': source_key}
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error copying object: {e}")
            return False, json.dumps({"error": str(e)})
