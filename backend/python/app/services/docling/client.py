import asyncio
import json
import os
from typing import Optional

import httpx

from app.config.constants.http_status_code import HttpStatusCode
from app.models.blocks import BlocksContainer
from app.utils.logger import create_logger
from app.utils.request_context import inject_request_headers


class DoclingClient:
    """Client for communicating with the Docling processing service"""

    def __init__(self, service_url: Optional[str] = None, timeout: float = 2400.0) -> None:
        self.service_url = (service_url or os.getenv("DOCLING_SERVICE_URL", "http://localhost:8081")).rstrip('/')

        self.timeout = timeout
        self.logger = create_logger(__name__)
        self.max_retries = 3
        self.retry_delay = 1.0  # seconds

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

    async def parse_pdf(self, record_name: str, pdf_binary: bytes) -> Optional[str]:
        """
        Parse PDF using the external Docling service (phase 1 - no block creation).

        Args:
            record_name: Name of the record/document
            pdf_binary: Binary PDF data

        Returns:
            Serialized parse result (JSON-encoded document) if successful, None if failed
        """
        if not isinstance(pdf_binary, bytes):
            self.logger.error(f"❌ Invalid pdf_binary type: expected bytes, got {type(pdf_binary).__name__}")
            return None

        if len(pdf_binary) > 100 * 1024 * 1024:  # 100MB limit
            self.logger.error(f"❌ PDF too large for processing: {len(pdf_binary)} bytes (max: 100MB)")
            return None

        timeout_config = httpx.Timeout(
            connect=30.0,
            read=self.timeout,
            write=60.0,
            pool=30.0
        )

        limits = httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=30.0
        )

        async with httpx.AsyncClient(
            timeout=timeout_config,
            limits=limits,
            http2=True
        ) as client:
            for attempt in range(self.max_retries):
                try:
                    self.logger.info(f"🚀 Sending PDF parse request for: {record_name} (attempt {attempt + 1}/{self.max_retries})")

                    response = await client.post(
                        f"{self.service_url}/parse-pdf",
                        data={"record_name": record_name},
                        files={"file": (record_name, pdf_binary, "application/pdf")},
                        headers=inject_request_headers({
                            "Connection": "keep-alive",
                            "Keep-Alive": "timeout=30"
                        })
                    )

                    if response.status_code == HttpStatusCode.SUCCESS.value:
                        result = await asyncio.to_thread(response.json)
                        if result.get("success"):
                            return result["parse_result"]
                        else:
                            error_msg = result.get("error", "Unknown error")
                            self.logger.error(f"❌ Docling service returned error for {record_name}: {error_msg}")
                            return None
                    else:
                        self.logger.error(f"❌ Docling service HTTP error {response.status_code}: {response.text}")
                        if response.status_code in [502, 503, 504]:
                            self.logger.warning(f"⚠️ Service temporarily unavailable (HTTP {response.status_code})")
                        if attempt < self.max_retries - 1:
                            delay = self.retry_delay * (2 ** attempt)
                            self.logger.info(f"🔄 Retrying in {delay} seconds...")
                            await asyncio.sleep(delay)
                            continue
                        return None

                except (httpx.TimeoutException, httpx.ConnectError, httpx.WriteError, httpx.RequestError) as e:
                    self.logger.error(f"❌ Error parsing PDF {record_name} (attempt {attempt + 1}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None
                except Exception as e:
                    self.logger.error(f"❌ Unexpected error parsing PDF {record_name} (attempt {attempt + 1}): {str(e)}")
                    self.logger.exception(e)
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None

            self.logger.error(f"❌ Failed to parse PDF {record_name} after {self.max_retries} attempts")
            return None

    async def create_blocks(self, parse_result: str, page_number: int = None) -> Optional[BlocksContainer]:
        """
        Create blocks from parse result using the external Docling service (phase 2).

        Args:
            parse_result: Serialized parse result from parse_pdf
            page_number: Optional page number for page-specific processing

        Returns:
            BlocksContainer if successful, None if failed
        """
        # Configure httpx with longer timeout for block creation (involves LLM calls)
        timeout_config = httpx.Timeout(
            connect=30.0,
            read=self.timeout,  # Full timeout for LLM processing
            write=30.0,
            pool=30.0
        )

        limits = httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=30.0
        )

        async with httpx.AsyncClient(
            timeout=timeout_config,
            limits=limits,
            http2=True
        ) as client:
            for attempt in range(self.max_retries):
                try:
                    payload = {
                        "parse_result": parse_result,
                        "page_number": page_number
                    }

                    self.logger.info(f"🚀 Sending create blocks request (attempt {attempt + 1}/{self.max_retries})")

                    response = await client.post(
                        f"{self.service_url}/create-blocks",
                        json=payload,
                        headers=inject_request_headers({
                            "Content-Type": "application/json",
                            "Connection": "keep-alive",
                            "Keep-Alive": "timeout=30"
                        })
                    )

                    if response.status_code == HttpStatusCode.SUCCESS.value:
                        result = await asyncio.to_thread(response.json)
                        if result.get("success"):
                            block_containers = await asyncio.to_thread(
                                self._parse_blocks_container,
                                result["block_containers"]
                            )
                            return block_containers
                        else:
                            error_msg = result.get("error", "Unknown error")
                            self.logger.error(f"❌ Docling service returned error: {error_msg}")
                            return None
                    else:
                        self.logger.error(f"❌ Docling service HTTP error {response.status_code}: {response.text}")
                        if response.status_code in [502, 503, 504]:
                            self.logger.warning(f"⚠️ Service temporarily unavailable (HTTP {response.status_code})")
                        if attempt < self.max_retries - 1:
                            delay = self.retry_delay * (2 ** attempt)
                            self.logger.info(f"🔄 Retrying in {delay} seconds...")
                            await asyncio.sleep(delay)
                            continue
                        return None

                except (httpx.TimeoutException, httpx.ConnectError, httpx.WriteError, httpx.RequestError) as e:
                    self.logger.error(f"❌ Error creating blocks (attempt {attempt + 1}): {str(e)}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None
                except Exception as e:
                    self.logger.error(f"❌ Unexpected error creating blocks (attempt {attempt + 1}): {str(e)}")
                    self.logger.exception(e)
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        self.logger.info(f"🔄 Retrying in {delay} seconds...")
                        await asyncio.sleep(delay)
                        continue
                    return None

            self.logger.error(f"❌ Failed to create blocks after {self.max_retries} attempts")
            return None

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
