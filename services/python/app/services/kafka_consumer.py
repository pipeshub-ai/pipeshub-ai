from confluent_kafka import Consumer, KafkaError
import json
import logging
from typing import List, Dict, Tuple, Set
import aiohttp
import asyncio
import os
from datetime import datetime, timedelta, timezone
from jose import jwt
from app.config.configuration_service import ConfigurationService, config_node_constants, KafkaConfig

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Kafka configuration
config_service = ConfigurationService()

# Concurrency control settings
MAX_CONCURRENT_TASKS = 5  # Maximum number of messages to process concurrently
RATE_LIMIT_PER_SECOND = 2  # Maximum number of new tasks to start per second

def generate_jwt(token_payload: dict) -> str:
    """
    Generate a JWT token using the jose library.
    
    Args:
        token_payload (dict): The payload to include in the JWT
        
    Returns:
        str: The generated JWT token
    """
    # Get the JWT secret from environment variable
    jwt_secret = os.getenv('SCOPED_JWT_SECRET')
    if not jwt_secret:
        raise ValueError("SCOPED_JWT_SECRET environment variable is not set")
    
    # Add standard claims if not present
    if 'exp' not in token_payload:
        # Set expiration to 1 hour from now
        token_payload['exp'] = datetime.now(timezone.utc) + timedelta(hours=1)
    
    if 'iat' not in token_payload:
        # Set issued at to current time
        token_payload['iat'] = datetime.now(timezone.utc)
    
    # Generate the JWT token using jose
    token = jwt.encode(token_payload, jwt_secret, algorithm='HS256')
    
    return token

async def make_api_call(signed_url_route: str, token: str) -> dict:
    """
    Make an API call with the JWT token.
    
    Args:
        signed_url_route (str): The route to send the request to
        token (str): The JWT token to use for authentication
        
    Returns:
        dict: The response from the API
    """        
    async with aiohttp.ClientSession() as session:
        url = signed_url_route
        
        logger.debug(f"Making API call to: {url}")
        # Add the JWT to the Authorization header
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        # Make the request
        async with session.get(url, headers=headers) as response:
            content_type = response.headers.get('Content-Type', '').lower()
            if response.status == 200 and 'application/json' in content_type:
                data = await response.json()
                return {'is_json': True, 'data': data}
            else:
                data = await response.read()
                return {'is_json': False, 'data': data}

class KafkaConsumerManager:
    def __init__(self, config_service, event_processor):
        self.consumer = None
        self.running = False
        self.event_processor = event_processor
        self.config_service = config_service
        # Concurrency control
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_TASKS)
        self.active_tasks: Set[asyncio.Task] = set()
        self.rate_limiter = RateLimiter(RATE_LIMIT_PER_SECOND)
        
        # Message tracking
        self.processed_messages: Dict[str, List[int]] = {}
        
    async def create_consumer(self):
        try:
            
            async def get_kafka_config():
                kafka_config = await self.config_service.get_config(config_node_constants.KAFKA.value)
                brokers = kafka_config['brokers']
                
                return {
                    'bootstrap.servers': ",".join(brokers),
                    'group.id': 'record_consumer_group',
                    'auto.offset.reset': 'earliest',
                    'enable.auto.commit': True,
                    'isolation.level': 'read_committed',
                    'enable.partition.eof': False,
                    'client.id': KafkaConfig.CLIENT_ID_MAIN.value
                }

            KAFKA_CONFIG = await get_kafka_config()
            # Topic to consume from
            KAFKA_TOPIC = 'record-events'

            self.consumer = Consumer(KAFKA_CONFIG)
            self.consumer.subscribe([KAFKA_TOPIC])
            logger.info(f"Successfully subscribed to topic: {KAFKA_TOPIC}")
        except Exception as e:
            logger.error(f"Failed to create consumer: {e}")
            logger.info(
                "Please ensure the topic 'record-events' exists on the Kafka broker")
            raise

    async def process_message_wrapper(self, message):
        """Wrapper to handle async task cleanup and semaphore release"""
        # Extract message identifiers for logging
        topic = message.topic()
        partition = message.partition()
        offset = message.offset()
        message_id = f"{topic}-{partition}-{offset}"
        
        try:
            logger.info(f"Starting to process message: {message_id}")
            success = await self._process_message(message)
            logger.info(f"Finished processing message {message_id}: {'Success' if success else 'Failed'}")
            return success
        except Exception as e:
            logger.error(f"Error in process_message_wrapper for {message_id}: {e}")
            return False
        finally:
            # Release the semaphore to allow a new task to start
            self.semaphore.release()

    async def _process_message(self, message):
        # Extract message identifiers once
        topic = message.topic()
        partition = message.partition()
        offset = message.offset()
        # Create a standardized message ID format
        topic_partition = f"{topic}-{partition}"
        message_id = f"{topic_partition}-{offset}"
        
        # Check for DUPLICATE processing first
        if self.is_message_processed(topic_partition, offset):
            logger.info(f"Message {message_id} already processed, skipping")
            return True
            
        try:
            message_value = message.value()
            if isinstance(message_value, bytes):
                message_value = message_value.decode('utf-8')

            # Parse JSON only once with proper error handling
            try:
                data = json.loads(message_value)
                # Handle nested JSON strings
                if isinstance(data, str):
                    data = json.loads(data)
                logger.debug(f"Parsed message data: {type(data)}")
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON: {e}")
                return False

            event_type = data.get('eventType')
            if not event_type:
                logger.error(f"Missing event_type for topic: {topic}")
                return False

            logger.info(f"Processing file record with event type: {event_type}")
            payload_data = data.get('payload')
            
            # Get signed URL from the route
            if payload_data and payload_data.get('signedUrlRoute'):
                try:
                    # Make request to get signed URL
                    payload = {
                        'orgId': payload_data['orgId'],
                        'scopes': ["storage:token"]
                    }
                    # Generate the JWT token
                    token = generate_jwt(payload)

                    # Make the API call with the token
                    response = await make_api_call(payload_data['signedUrlRoute'], token)
                    logger.debug(f"Signed URL response received")
                    
                    if response.get('is_json') is True:
                        response_data = response.get('data')
                        signed_url = response_data['signedUrl']
                        # Process the file using signed URL
                        payload_data['signedUrl'] = signed_url
                        data["payload"] = payload_data
                    else:
                        response_data = response.get('data')
                        payload_data['buffer'] = response_data

                    await self.event_processor.on_event(data)
                    logger.info(f"✅ Successfully processed document for event: {event_type}")
                    
                    # Mark as processed after successful processing
                    self.mark_message_processed(topic_partition, offset)
                    return True
                    
                except Exception as e:
                    logger.error(f"Error getting signed URL: {str(e)}")
                    return False
            else:
                logger.warning(f"No signedUrlRoute found in payload")
                return False
                
        except Exception as e:
            logger.error(f"Error processing message {message_id}: {e}")
            return False

    def is_message_processed(self, topic_partition: str, offset: int) -> bool:
        """Check if a message has already been processed."""
        return (topic_partition in self.processed_messages and
                offset in self.processed_messages[topic_partition])

    def mark_message_processed(self, topic_partition: str, offset: int):
        """Mark a message as processed."""
        if topic_partition not in self.processed_messages:
            self.processed_messages[topic_partition] = []
        self.processed_messages[topic_partition].append(offset)
    
    def cleanup_completed_tasks(self):
        """Remove completed tasks from the active tasks set"""
        done_tasks = {task for task in self.active_tasks if task.done()}
        self.active_tasks -= done_tasks
        
        # Check for exceptions in completed tasks
        for task in done_tasks:
            if task.exception():
                logger.error(f"Task completed with exception: {task.exception()}")

    async def start_processing_task(self, message):
        """Start a new task for processing a message with semaphore control"""
        # Wait for the rate limiter
        await self.rate_limiter.wait()
        
        # Wait for a semaphore slot to become available
        await self.semaphore.acquire()
        
        # Create and start a new task
        task = asyncio.create_task(self.process_message_wrapper(message))
        self.active_tasks.add(task)
        
        # Clean up completed tasks
        self.cleanup_completed_tasks()
        
        # Log current task count
        logger.debug(f"Active tasks: {len(self.active_tasks)}/{MAX_CONCURRENT_TASKS}")

    async def consume_messages(self):
        """Main consumption loop."""
        try:
            logger.info("Starting Kafka consumer loop")
            while self.running:
                try:
                    message = self.consumer.poll(0.1)  # Poll with a shorter timeout

                    if message is None:
                        # Wait a bit before the next poll if no message was received
                        await asyncio.sleep(0.1)
                        continue

                    if message.error():
                        if message.error().code() == KafkaError._PARTITION_EOF:
                            continue
                        else:
                            logger.error(f"Kafka error: {message.error()}")
                            continue
                    
                    # Start processing the message in a new task
                    await self.start_processing_task(message)
                    
                except asyncio.CancelledError:
                    logger.info("Kafka consumer task cancelled")
                    break
                except Exception as e:
                    logger.error(f"Error in consume_messages loop: {e}")
                    await asyncio.sleep(1)  # Wait a bit before retrying
        except Exception as e:
            logger.error(f"Fatal error in consume_messages: {e}")
        finally:
            # Wait for all active tasks to complete before shutting down
            if self.active_tasks:
                logger.info(f"Waiting for {len(self.active_tasks)} active tasks to complete...")
                await asyncio.gather(*self.active_tasks, return_exceptions=True)
                
            if self.consumer:
                self.consumer.close()
                logger.info("Kafka consumer closed")

    async def start(self):
        """Start the consumer."""
        self.running = True
        await self.create_consumer()

    def stop(self):
        """Stop the consumer."""
        self.running = False


class RateLimiter:
    """Simple rate limiter to control how many tasks start per second"""
    
    def __init__(self, rate_limit_per_second):
        self.rate = rate_limit_per_second
        self.last_check = datetime.now()
        self.tokens = rate_limit_per_second
        self.lock = asyncio.Lock()
    
    async def wait(self):
        """Wait until a token is available"""
        async with self.lock:
            while True:
                now = datetime.now()
                time_passed = (now - self.last_check).total_seconds()
                
                # Add new tokens based on time passed
                self.tokens += time_passed * self.rate
                self.last_check = now
                
                # Cap tokens at the maximum rate
                if self.tokens > self.rate:
                    self.tokens = self.rate
                
                if self.tokens >= 1:
                    # Consume a token
                    self.tokens -= 1
                    break
                
                # Wait for some tokens to accumulate
                await asyncio.sleep(1.0 / self.rate)