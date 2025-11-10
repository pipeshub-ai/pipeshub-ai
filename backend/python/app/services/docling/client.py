import asyncio
import base64
import json
import os
from typing import Optional

import httpx

from app.config.constants.http_status_code import HttpStatusCode
from app.models.blocks import BlocksContainer
from app.utils.logger import create_logger


class DoclingClient:
    """Client for communicating with the Docling processing service"""

    def __init__(self, service_url: Optional[str] = None, timeout: float = 2400.0) -> None:
        self.service_url = (service_url or os.getenv("DOCLING_SERVICE_URL", "http://localhost:8081")).rstrip('/')

        self.timeout = timeout
        self.logger = create_logger(__name__)
        self.max_retries = 3
        self.retry_delay = 1.0  # seconds

    async def process_pdf(self, record_name: str, pdf_binary: bytes, org_id: Optional[str] = None) -> Optional[BlocksContainer]:
        """
        Process PDF using the external Docling service with retry logic

        Args:
            record_name: Name of the record/document
            pdf_binary: Binary PDF data
            org_id: Optional organization ID

        Returns:
            BlocksContainer if successful, None if failed
        """
        # Validate pdf_binary type
        if not isinstance(pdf_binary, bytes):
            self.logger.error(f"❌ Invalid pdf_binary type: expected bytes, got {type(pdf_binary).__name__}")
            return None

        # Validate payload size
        if len(pdf_binary) > 100 * 1024 * 1024:  # 100MB limit
            self.logger.error(f"❌ PDF too large for processing: {len(pdf_binary)} bytes (max: 100MB)")
            return None

        # Configure httpx with proper connection settings
        timeout_config = httpx.Timeout(
            connect=30.0,  # Connection timeout
            read=self.timeout,  # Read timeout
            write=30.0,  # Write timeout
            pool=30.0  # Pool timeout
        )

        # Use connection pooling and keep-alive
        limits = httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=30.0
        )

        # Create client once and reuse for all retry attempts
        async with httpx.AsyncClient(
            timeout=timeout_config,
            limits=limits,
            http2=True  # Enable HTTP/2 for better performance
        ) as client:
            for attempt in range(self.max_retries):
                try:
                    # Encode PDF binary to base64
                    pdf_base64 = base64.b64encode(pdf_binary).decode('utf-8')

                    # Prepare request payload
                    payload = {
                        "record_name": record_name,
                        "pdf_binary": pdf_base64,
                        "org_id": org_id
                    }

                    self.logger.info(f"🚀 Sending PDF processing request for: {record_name} (attempt {attempt + 1}/{self.max_retries})")

                    response = await client.post(
                        f"{self.service_url}/process-pdf",
                        json=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Connection": "keep-alive",
                            "Keep-Alive": "timeout=30"
                        }
                    )

                    if response.status_code == HttpStatusCode.SUCCESS.value:
                        # Parse JSON response in thread pool to avoid blocking
                        result = await asyncio.to_thread(response.json)
                        if result.get("success"):
                            # Run heavy JSON parsing and object creation in thread pool
                            block_containers = await asyncio.to_thread(
                                self._parse_blocks_container,
                                result["block_containers"]
                            )
                            return block_containers
                        else:
                            error_msg = result.get("error", "Unknown error")
                            self.logger.error(f"❌ Docling service returned error for {record_name}: {error_msg}")
                            return None
                    else:
                        self.logger.error(f"❌ Docling service HTTP error {response.status_code}: {response.text}")

                        # Check if it's a service unavailable error
                        if response.status_code in [502, 503, 504]:
                            self.logger.warning(f"⚠️ Service temporarily unavailable (HTTP {response.status_code})")

                        if attempt < self.max_retries - 1:
                            delay = self.retry_delay * (2 ** attempt)
                            self.logger.info(f"🔄 Retrying in {delay} seconds...")
                            await asyncio.sleep(delay)
                            continue
                        return None

                except httpx.TimeoutException as e:
                    self.logger.error(f"❌ Timeout processing PDF {record_name} (attempt {attempt + 1}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None
                except httpx.ConnectError as e:
                    self.logger.error(f"❌ Connection error processing PDF {record_name} (attempt {attempt + 1}): {str(e)}")
                    self.logger.warning("⚠️ Service appears to be down (connection refused)")

                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Service may be starting up. Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None
                except httpx.WriteError as e:
                    # Handle the specific "write could not complete without blocking" error
                    self.logger.error(f"❌ Write error processing PDF {record_name} (attempt {attempt + 1}): {str(e)}")
                    if "write could not complete without blocking" in str(e):
                        self.logger.warning("⚠️ Network buffer issue detected, retrying with exponential backoff...")
                        if attempt < self.max_retries - 1:
                            # Exponential backoff for write errors
                            delay = self.retry_delay * (2 ** attempt)
                            self.logger.info(f"🔄 Retrying in {delay} seconds...")
                            await asyncio.sleep(delay)
                            continue
                    return None
                except httpx.RequestError as e:
                    self.logger.error(f"❌ Request error processing PDF {record_name} (attempt {attempt + 1}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None
                except Exception as e:
                    self.logger.error(f"❌ Unexpected error processing PDF {record_name} (attempt {attempt + 1}): {str(e)}")
                    self.logger.exception(e)  # Log full traceback for debugging
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None

            self.logger.error(f"❌ Failed to process PDF {record_name} after {self.max_retries} attempts")
            return None

    def _parse_blocks_container(self, block_containers_data) -> BlocksContainer:
        """
        Create BlocksContainer object from dictionary or JSON string.
        This method runs in a thread pool to avoid blocking the event loop.
        """
        try:
            # Handle both dict and JSON string cases
            if isinstance(block_containers_data, str):
                block_containers_dict = json.loads(block_containers_data)
            else:
                block_containers_dict = block_containers_data

            return BlocksContainer(**block_containers_dict)
        except Exception as e:
            self.logger.error(f"❌ Failed to parse blocks container: {str(e)}")
            raise

    async def _check_service_health(self, client: httpx.AsyncClient) -> bool:
        """
        Internal method to check service health using an existing client

        Args:
            client: Existing httpx.AsyncClient instance

        Returns:
            True if service is healthy, False otherwise
        """
        try:
            response = await client.get(
                f"{self.service_url}/health",
                timeout=10.0
            )
            is_healthy = response.status_code == HttpStatusCode.SUCCESS.value
            if is_healthy:
                self.logger.info("✅ Docling service is healthy")
            else:
                self.logger.warning(f"⚠️ Docling service health check returned status {response.status_code}")
            return is_healthy
        except httpx.ConnectError:
            self.logger.error("❌ Cannot connect to Docling service - service appears to be down")
            return False
        except Exception as e:
            self.logger.error(f"❌ Health check failed: {str(e)}")
            return False

    async def health_check(self) -> bool:
        """Check if the Docling service is healthy"""
        try:
            timeout_config = httpx.Timeout(
                connect=10.0,
                read=10.0,
                write=10.0,
                pool=10.0
            )

            async with httpx.AsyncClient(timeout=timeout_config) as client:
                return await self._check_service_health(client)
        except Exception as e:
            self.logger.error(f"❌ Health check failed: {str(e)}")
            return False
