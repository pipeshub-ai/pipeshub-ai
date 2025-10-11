from typing import Any, Dict, Optional, List
from dataclasses import dataclass, asdict
import json
from botocore.exceptions import BotoCoreError, ClientError

from app.sources.client.amazon_sqs import AmazonSQSClient


@dataclass
class AmazonSQSResponse:
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class AmazonSQSDataSource:
    """
    Amazon SQS Data Source
    Provides async-compatible methods that wrap SQS client operations.
    """

    def __init__(self, sqs_client: AmazonSQSClient) -> None:
        self._client = sqs_client

    def get_client(self) -> AmazonSQSClient:
        return self._client

    async def send_message(
        self,
        queue_url: str,
        message_body: str,
        attributes: Optional[Dict[str, Any]] = None,
        delay_seconds: Optional[int] = None,
        message_group_id: Optional[str] = None,
        message_deduplication_id: Optional[str] = None,
    ) -> AmazonSQSResponse:
        try:
            params: Dict[str, Any] = {"QueueUrl": queue_url, "MessageBody": message_body}
            if attributes:
                params["MessageAttributes"] = attributes
            if delay_seconds is not None:
                params["DelaySeconds"] = delay_seconds
            if message_group_id is not None:
                params["MessageGroupId"] = message_group_id
            if message_deduplication_id is not None:
                params["MessageDeduplicationId"] = message_deduplication_id

            resp = self._client.get_client().send_message(**params)
            return AmazonSQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return AmazonSQSResponse(success=False, error=str(e))
        except Exception as e:
            return AmazonSQSResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def receive_message(
        self,
        queue_url: str,
        max_messages: int = 1,
        wait_time: int = 0,
        visibility_timeout: Optional[int] = None,
        attribute_names: Optional[List[str]] = None,
        message_attribute_names: Optional[List[str]] = None,
    ) -> AmazonSQSResponse:
        try:
            params: Dict[str, Any] = {
                "QueueUrl": queue_url,
                "MaxNumberOfMessages": max_messages,
                "WaitTimeSeconds": wait_time,
            }
            if visibility_timeout is not None:
                params["VisibilityTimeout"] = visibility_timeout
            if attribute_names:
                params["AttributeNames"] = attribute_names
            if message_attribute_names:
                params["MessageAttributeNames"] = message_attribute_names

            resp = self._client.get_client().receive_message(**params)
            return AmazonSQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return AmazonSQSResponse(success=False, error=str(e))
        except Exception as e:
            return AmazonSQSResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def delete_message(self, queue_url: str, receipt_handle: str) -> AmazonSQSResponse:
        try:
            resp = self._client.get_client().delete_message(
                QueueUrl=queue_url, ReceiptHandle=receipt_handle
            )
            return AmazonSQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return AmazonSQSResponse(success=False, error=str(e))
        except Exception as e:
            return AmazonSQSResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def list_queues(self, prefix: Optional[str] = None) -> AmazonSQSResponse:
        try:
            params = {}
            if prefix:
                params["QueueNamePrefix"] = prefix
            resp = self._client.get_client().list_queues(**params)
            return AmazonSQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return AmazonSQSResponse(success=False, error=str(e))
        except Exception as e:
            return AmazonSQSResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_queue_url(self, queue_name: str) -> AmazonSQSResponse:
        try:
            resp = self._client.get_client().get_queue_url(QueueName=queue_name)
            return AmazonSQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return AmazonSQSResponse(success=False, error=str(e))
        except Exception as e:
            return AmazonSQSResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def get_queue_attributes(self, queue_url: str, attribute_names: Optional[List[str]] = None) -> AmazonSQSResponse:
        try:
            params = {"QueueUrl": queue_url}
            if attribute_names:
                params["AttributeNames"] = attribute_names
            resp = self._client.get_client().get_queue_attributes(**params)
            return AmazonSQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return AmazonSQSResponse(success=False, error=str(e))
        except Exception as e:
            return AmazonSQSResponse(success=False, error=f"Unexpected error: {str(e)}")

    async def purge_queue(self, queue_url: str) -> AmazonSQSResponse:
        try:
            resp = self._client.get_client().purge_queue(QueueUrl=queue_url)
            return AmazonSQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return AmazonSQSResponse(success=False, error=str(e))
        except Exception as e:
            return AmazonSQSResponse(success=False, error=f"Unexpected error: {str(e)}")
