import json
from datetime import datetime, timedelta

from redis import asyncio as aioredis  # type: ignore


class RedisScheduler:
    def __init__(self, redis_url: str, logger, delay_hours: int = 1) -> None:
        self.redis = aioredis.from_url(redis_url)
        self.logger = logger
        self.delay_hours = delay_hours
        self.scheduled_set = "scheduled_updates"
        self.processing_set = "processing_updates"

    async def schedule_update(self, event_data: dict) -> None:
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

    async def get_ready_events(self) -> list:
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
