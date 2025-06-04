# import asyncio
# import json
# import logging
# from typing import Any, Dict

# from aiokafka import AIOKafkaProducer

# from app.utils.time_conversion import get_epoch_timestamp_in_ms

# # Configure logging
# logging.basicConfig(level=logging.INFO)
# logger = logging.getLogger(__name__)


# class KafkaTestProducer:
#     def __init__(self, bootstrap_servers: str = "localhost:9092"):
#         self.config = {
#             "bootstrap_servers": bootstrap_servers,  # aiokafka uses bootstrap_servers
#             "client_id": "test_producer",
#         }
#         self.producer = None

#     async def start(self):
#         """Initialize and start the producer"""
#         try:
#             self.producer = AIOKafkaProducer(**self.config)
#             await self.producer.start()
#             logger.info("✅ Kafka test producer started successfully")
#         except Exception as e:
#             logger.error(f"❌ Failed to start Kafka producer: {e}")
#             raise

#     async def stop(self):
#         """Stop the producer and clean up resources"""
#         if self.producer:
#             try:
#                 await self.producer.stop()
#                 logger.info("✅ Kafka test producer stopped successfully")
#             except Exception as e:
#                 logger.error(f"❌ Error stopping Kafka producer: {e}")

#     async def send_message(self, topic: str, message: Dict[str, Any]):
#         """Send a message to a specific topic"""
#         try:
#             if not self.producer:
#                 raise RuntimeError("Producer not started. Call start() first.")

#             # Convert message to JSON bytes
#             message_bytes = json.dumps(message).encode("utf-8")

#             # Send message and wait for delivery
#             record_metadata = await self.producer.send_and_wait(
#                 topic=topic,
#                 value=message_bytes
#             )

#             logger.info(
#                 f"✅ Message delivered to {record_metadata.topic} "
#                 f"[{record_metadata.partition}] at offset {record_metadata.offset}"
#             )
#             logger.info(f"Message sent to topic {topic}: {message}")
#             return True
#         except Exception as e:
#             logger.error(f"❌ Error sending message: {e}")
#             return False

#     async def test_org_events(self):
#         """Test organization-related events"""
#         current_timestamp = get_epoch_timestamp_in_ms()  # Convert to milliseconds
#         org_id = "org_12345"

#         # Create org event
#         create_event = {
#             "eventType": "orgCreated",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id,
#                 "accountType": "enterprise",
#                 "registeredName": "PipesHub",
#             },
#         }
#         await self.send_message("entity-events", create_event)

#         # Wait 10 seconds
#         logger.info("Waiting 10 seconds before sending update event...")
#         await asyncio.sleep(10)

#         # Update org event
#         current_timestamp = get_epoch_timestamp_in_ms()
#         update_event = {
#             "eventType": "orgUpdated",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id,
#                 "accountType": "individual",
#                 "registeredName": "Acme Corporation Updated"
#             }
#         }
#         await self.send_message('entity-events', update_event)

#         # Wait 10 seconds
#         logger.info("Waiting 10 seconds before sending delete event...")
#         await asyncio.sleep(10)

#         # Delete org event
#         current_timestamp = get_epoch_timestamp_in_ms()
#         delete_event = {
#             "eventType": "orgDeleted",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id
#             }
#         }
#         await self.send_message('entity-events', delete_event)

#     async def test_user_events(self):
#         """Test user-related events"""
#         current_timestamp = get_epoch_timestamp_in_ms()
#         org_id = "org_12345"
#         user_id = "user_67890"

#         # Add user event
#         user_add_event = {
#             "eventType": "userAdded",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id,
#                 "userId": user_id,
#                 "email": "test@example.com",
#                 "name": "Test User"
#             }
#         }
#         await self.send_message("entity-events", user_add_event)

#         # Wait 5 seconds
#         logger.info("Waiting 5 seconds before sending user update event...")
#         await asyncio.sleep(5)

#         # Update user event
#         current_timestamp = get_epoch_timestamp_in_ms()
#         user_update_event = {
#             "eventType": "userUpdated",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id,
#                 "userId": user_id,
#                 "email": "updated@example.com",
#                 "name": "Updated Test User"
#             }
#         }
#         await self.send_message("entity-events", user_update_event)

#         # Wait 5 seconds
#         logger.info("Waiting 5 seconds before sending user delete event...")
#         await asyncio.sleep(5)

#         # Delete user event
#         current_timestamp = get_epoch_timestamp_in_ms()
#         user_delete_event = {
#             "eventType": "userDeleted",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id,
#                 "userId": user_id
#             }
#         }
#         await self.send_message("entity-events", user_delete_event)

#     async def test_app_events(self):
#         """Test app-related events"""
#         current_timestamp = get_epoch_timestamp_in_ms()
#         org_id = "org_12345"

#         # App enabled event
#         app_enabled_event = {
#             "eventType": "appEnabled",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id,
#                 "appName": "google-drive",
#                 "connectorType": "storage"
#             }
#         }
#         await self.send_message("entity-events", app_enabled_event)

#         # Wait 5 seconds
#         logger.info("Waiting 5 seconds before sending app disabled event...")
#         await asyncio.sleep(5)

#         # App disabled event
#         current_timestamp = get_epoch_timestamp_in_ms()
#         app_disabled_event = {
#             "eventType": "appDisabled",
#             "timestamp": current_timestamp,
#             "payload": {
#                 "orgId": org_id,
#                 "appName": "google-drive",
#                 "connectorType": "storage"
#             }
#         }
#         await self.send_message("entity-events", app_disabled_event)

#     async def __aenter__(self):
#         """Async context manager entry"""
#         await self.start()
#         return self

#     async def __aexit__(self, exc_type, exc_val, exc_tb):
#         """Async context manager exit"""
#         await self.stop()


# async def main():
#     """Main function to run all tests"""
#     async with KafkaTestProducer() as producer:
#         # Test org events
#         logger.info("🚀 Testing org events...")
#         await producer.test_org_events()

#         logger.info("⏳ Waiting 5 seconds between test suites...")
#         await asyncio.sleep(5)

#         # Test user events
#         logger.info("🚀 Testing user events...")
#         await producer.test_user_events()

#         logger.info("⏳ Waiting 5 seconds between test suites...")
#         await asyncio.sleep(5)

#         # Test app events
#         logger.info("🚀 Testing app events...")
#         await producer.test_app_events()

#         logger.info("✅ All tests completed successfully!")


# if __name__ == "__main__":
#     asyncio.run(main())
