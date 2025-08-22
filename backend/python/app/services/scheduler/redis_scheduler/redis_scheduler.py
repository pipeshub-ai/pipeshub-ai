import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiohttp  # type: ignore
from jose import jwt
from redis import asyncio as aioredis  # type: ignore
from tenacity import retry, stop_after_attempt, wait_exponential  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    CollectionNames,
    ProgressStatus,
)
from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import config_node_constants
from app.events.events import EventProcessor
from app.services.scheduler.interface.scheduler import Scheduler


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=15))
async def make_api_call(signed_url_route: str, token: str) -> dict:
    """
    Make an API call with the JWT token.

    Args:
        signed_url_route (str): The route to send the request to
        token (str): The JWT token to use for authentication

    Returns:
        dict: The response from the API
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = signed_url_route

            # Add the JWT to the Authorization header
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Make the request
            async with session.get(url, headers=headers) as response:
                content_type = response.headers.get("Content-Type", "").lower()

                if response.status == HttpStatusCode.SUCCESS.value and "application/json" in content_type:
                    data = await response.json()
                    return {"is_json": True, "data": data}
                else:
                    data = await response.read()
                    return {"is_json": False, "data": data}
    except Exception:
        raise


class RedisScheduler(Scheduler):
    def __init__(self, redis_url: str, logger, config_service: ConfigurationService, delay_hours: int = 1) -> None:
        self.redis = aioredis.from_url(redis_url)
        self.logger = logger
        self.config_service = config_service
        self.delay_hours = delay_hours
        self.scheduled_set = "scheduled_updates"
        self.processing_set = "processing_updates"

    async def generate_jwt(self, token_payload: dict) -> str:
        """
        Generate a JWT token using the jose library.

        Args:
            token_payload (dict): The payload to include in the JWT

        Returns:
            str: The generated JWT token
        """
        # Get the JWT secret from environment variable
        secret_keys = await self.config_service.get_config(
            config_node_constants.SECRET_KEYS.value
        )
        if not secret_keys:
            raise ValueError("SECRET_KEYS environment variable is not set")
        scoped_jwt_secret = secret_keys.get("scopedJwtSecret") # type: ignore
        if not scoped_jwt_secret:
            raise ValueError("SCOPED_JWT_SECRET environment variable is not set")

        # Add standard claims if not present
        if "exp" not in token_payload:
            # Set expiration to 1 hour from now
            token_payload["exp"] = datetime.now(timezone.utc) + timedelta(hours=1)

        if "iat" not in token_payload:
            # Set issued at to current time
            token_payload["iat"] = datetime.now(timezone.utc)

        # Generate the JWT token using jose
        token = jwt.encode(token_payload, scoped_jwt_secret, algorithm="HS256")

        return token

    # implementing the abstract methods from the interface
    async def schedule_event(self, event_data: dict) -> None:
        """
        Schedule an update event for later processing.
        If an update for the same record already exists, it will be replaced.
        """
        try:
            record_id = event_data.get('payload', {}).get('recordId')
            if not record_id:
                raise ValueError("Event data missing recordId")

            # Calculate execution time
            execution_time = datetime.now() + timedelta(hours=self.delay_hours)

            # Create a composite key with record_id to ensure uniqueness
            event_json = json.dumps({
                'record_id': record_id,
                'scheduled_at': datetime.now().isoformat(),
                'event_data': event_data
            })

            # Remove any existing updates for this record
            existing_updates = await self.redis.zrangebyscore(
                self.scheduled_set,
                "-inf",
                "+inf"
            )
            for update in existing_updates:
                update_data = json.loads(update)
                if update_data.get('record_id') == record_id:
                    await self.redis.zrem(self.scheduled_set, update)
                    self.logger.info(f"Removed existing scheduled update for record {record_id}")

            # Store new update
            await self.redis.zadd(
                self.scheduled_set,
                {event_json: execution_time.timestamp()}
            )

            self.logger.info(
                f"Scheduled update for record {record_id} at {execution_time}"
            )
        except Exception as e:
            self.logger.error(f"Failed to schedule update: {str(e)}")
            raise

    # implementing the abstract methods from the interface
    async def get_scheduled_events(self) -> list:
        """Get events that are ready for processing"""
        try:
            current_time = datetime.now().timestamp()

            # Get events with scores (execution time) less than current time
            events = await self.redis.zrangebyscore(
                self.scheduled_set,
                "-inf",
                current_time
            )

            # Extract the actual event data from the stored format
            return [json.loads(event)['event_data'] for event in events]
        except Exception as e:
            self.logger.error(f"Failed to get ready events: {str(e)}")
            return []

    # implementing the abstract methods from the interface
    async def remove_processed_event(self, event_data: dict) -> None:
        """Remove an event after processing"""
        try:
            record_id = event_data.get('payload', {}).get('recordId')
            if not record_id:
                raise ValueError("Event data missing recordId")

            # Find and remove the event with matching record_id
            existing_updates = await self.redis.zrangebyscore(
                self.scheduled_set,
                "-inf",
                "+inf"
            )
            for update in existing_updates:
                update_data = json.loads(update)
                if update_data.get('record_id') == record_id:
                    await self.redis.zrem(self.scheduled_set, update)
                    self.logger.info(f"Removed processed event for record {record_id}")
                    break
        except Exception as e:
            self.logger.error(f"Failed to remove processed event: {str(e)}")

    # implementing the abstract methods from the interface
    async def process_scheduled_events(self, event_processor: EventProcessor) -> None:
        """Process scheduled events"""
        while True:
            try:
                # Get ready events
                ready_events = await self.get_scheduled_events()

                for event in ready_events:
                    try:
                        # Process the event
                        payload_data = event.get("payload", {})
                        record_id = payload_data.get("recordId")
                        extension = payload_data.get("extension", "unknown")
                        mime_type = payload_data.get("mimeType", "unknown")

                        if extension is None and mime_type != "text/gmail_content":
                            extension = payload_data["recordName"].split(".")[-1]

                        self.logger.info(
                            f"Processing update for record {record_id}"
                            f"Extension: {extension}, Mime Type: {mime_type}"
                        )

                        record = await event_processor.arango_service.get_document(
                            record_id, CollectionNames.RECORDS.value
                        )
                        if record is None:
                            self.logger.error(f"❌ Record {record_id} not found in database")
                            return
                        doc = dict(record)

                        # Update with new metadata fields
                        doc.update(
                            {
                                "indexingStatus": ProgressStatus.IN_PROGRESS.value,
                                "extractionStatus": ProgressStatus.IN_PROGRESS.value,
                            }
                        )

                        docs = [doc]
                        await event_processor.arango_service.batch_upsert_nodes(
                            docs, CollectionNames.RECORDS.value
                        )

                        if payload_data and payload_data.get("signedUrlRoute"):
                            try:
                                payload = {
                                    "orgId": payload_data["orgId"],
                                    "scopes": ["storage:token"],
                                }
                                token = await self.generate_jwt(payload)
                                self.logger.debug(f"Generated JWT token for record {record_id}")

                                response = await make_api_call(
                                    payload_data["signedUrlRoute"], token
                                )
                                self.logger.debug(
                                    f"Received signed URL response for record {record_id}"
                                )

                                if response.get("is_json"):
                                    signed_url = response["data"]["signedUrl"]
                                    payload_data["signedUrl"] = signed_url
                                else:
                                    payload_data["buffer"] = response["data"]
                                event["payload"] = payload_data

                                await event_processor.on_event(event)

                            except Exception as e:
                                self.logger.error(f"Error processing signed URL: {str(e)}")
                                raise

                        # Remove processed event
                        await self.remove_processed_event(event)

                        self.logger.info(
                            f"Processed scheduled update for record "
                            f"{event.get('payload', {}).get('recordId')}"
                        )
                    except Exception as e:
                        self.logger.error(f"Error processing scheduled update: {str(e)}")

                # Wait before next check
                await asyncio.sleep(60)  # Check every minute

            except Exception as e:
                self.logger.error(f"Error in scheduled update processor: {str(e)}")
                await asyncio.sleep(60)
