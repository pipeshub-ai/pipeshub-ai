import json
from dataclasses import asdict, dataclass
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class SQSResponse:
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class AmazonSQSClient:
    def __init__(self, access_key: str, secret_key: str, region_name: str):
        self.client = boto3.client(
            "sqs",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region_name,
        )

    def get_client(self) -> boto3.client:
        return self.client

    def send_message(self, queue_url: str, message_body: str, attributes: dict[str, Any] | None = None) -> SQSResponse:
        try:
            resp = self.client.send_message(
                QueueUrl=queue_url,
                MessageBody=message_body,
                MessageAttributes=attributes or {},
            )
            return SQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return SQSResponse(success=False, error=str(e))

    def receive_message(self, queue_url: str, max_messages: int = 1, wait_time: int = 0) -> SQSResponse:
        try:
            resp = self.client.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_time,
            )
            return SQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return SQSResponse(success=False, error=str(e))

    def delete_message(self, queue_url: str, receipt_handle: str) -> SQSResponse:
        try:
            resp = self.client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
            return SQSResponse(success=True, data=resp)
        except (BotoCoreError, ClientError) as e:
            return SQSResponse(success=False, error=str(e))

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> "AmazonSQSClient":
        config = await config_service.get_integration_config(org_id, user_id, "amazon_sqs")
        return cls(
            access_key=config.get("access_key"),
            secret_key=config.get("secret_key"),
            region_name=config.get("region_name", "us-east-1"),
            session_token=config.get("session_token"),
        )
