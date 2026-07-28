import asyncio
import json
import os
from typing import Optional

import httpx

from app.config.constants.http_status_code import HttpStatusCode
from app.models.blocks import BlocksContainer
from app.utils.logger import create_logger
from app.utils.request_context import inject_request_headers

MAX_PDF_BYTES = 100 * 1024 * 1024
SERVICE_UNAVAILABLE_STATUS_CODES = (502, 503, 504)


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

    def _validate_pdf_binary(self, pdf_binary: bytes) -> bool:
        if not isinstance(pdf_binary, bytes):
            self.logger.error(f"❌ Invalid pdf_binary type: expected bytes, got {type(pdf_binary).__name__}")
            return False

        if len(pdf_binary) > MAX_PDF_BYTES:
            self.logger.error(f"❌ PDF too large for processing: {len(pdf_binary)} bytes (max: {MAX_PDF_BYTES} bytes)")
            return False

        return True

    async def _sleep_before_retry(self, attempt: int) -> bool:
        """Back off before the next attempt; returns False once retries are exhausted."""
        if attempt >= self.max_retries - 1:
            return False

        delay = self.retry_delay * (2 ** attempt)
        self.logger.info(f"🔄 Retrying in {delay} seconds...")
        await asyncio.sleep(delay)
        return True

    async def _post_with_retry(
        self,
        path: str,
        description: str,
        *,
        json_body: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        write_timeout: float = 30.0,
    ) -> dict | None:
        """POST to the Docling service with exponential backoff and return the decoded JSON body.

        Transport errors and non-2xx responses are retried; an HTTP 200 carrying an
        application-level failure is handed back to the caller to interpret.
        """
        timeout_config = httpx.Timeout(
            connect=30.0,
            read=self.timeout,
            write=write_timeout,
            pool=30.0
        )

        limits = httpx.Limits(
            max_keepalive_connections=5,
            max_connections=10,
            keepalive_expiry=30.0
        )

        headers = inject_request_headers({
            "Connection": "keep-alive",
            "Keep-Alive": "timeout=30"
        })
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        async with httpx.AsyncClient(
            timeout=timeout_config,
            limits=limits,
            http2=True
        ) as client:
            for attempt in range(self.max_retries):
                self.logger.info(f"🚀 {description} (attempt {attempt + 1}/{self.max_retries})")
                try:
                    response = await client.post(
                        f"{self.service_url}{path}",
                        json=json_body,
                        data=data,
                        files=files,
                        headers=headers,
                    )
                except Exception as e:
                    self.logger.error(f"❌ {description} failed (attempt {attempt + 1}): {str(e)}")
                    if not await self._sleep_before_retry(attempt):
                        break
                    continue

                if response.status_code == HttpStatusCode.SUCCESS.value:
                    return await asyncio.to_thread(response.json)

                self.logger.error(f"❌ {description} returned HTTP {response.status_code}: {response.text}")
                if response.status_code in SERVICE_UNAVAILABLE_STATUS_CODES:
                    self.logger.warning(f"⚠️ Docling service temporarily unavailable (HTTP {response.status_code})")
                if not await self._sleep_before_retry(attempt):
                    break

        self.logger.error(f"❌ {description} failed after {self.max_retries} attempts")
        return None

    async def process_pdf(self, record_name: str, pdf_binary: bytes) -> Optional[BlocksContainer]:
        """Parse a PDF and build its blocks via the Docling service in a single request.

        The service parses the PDF in page batches internally, so the binary is uploaded
        once regardless of page count.

        Returns:
            BlocksContainer if successful, None if failed
        """
        if not self._validate_pdf_binary(pdf_binary):
            return None

        result = await self._post_with_retry(
            "/process-pdf",
            f"Processing PDF {record_name}",
            data={"record_name": record_name},
            files={"file": (record_name, pdf_binary, "application/pdf")},
            write_timeout=60.0,
        )
        if result is None:
            return None
        if not result.get("success"):
            self.logger.error(f"❌ Docling service returned error for {record_name}: {result.get('error', 'Unknown error')}")
            return None

        return await asyncio.to_thread(self._parse_blocks_container, result["block_containers"])

    async def parse_pdf(
        self,
        record_name: str,
        pdf_binary: bytes,
        page_range: tuple[int, int] | None = None,
    ) -> Optional[str]:
        """
        Parse PDF using the external Docling service (phase 1 - no block creation).

        Args:
            record_name: Name of the record/document
            pdf_binary: Binary PDF data
            page_range: Optional 1-based inclusive (start, end) page range.

        Returns:
            Serialized parse result (JSON-encoded document) if successful, None if failed
        """
        if not self._validate_pdf_binary(pdf_binary):
            return None

        form_data: dict = {"record_name": record_name}
        if page_range is not None:
            form_data["start_page"] = str(page_range[0])
            form_data["end_page"] = str(page_range[1])

        result = await self._post_with_retry(
            "/parse-pdf",
            f"Parsing PDF {record_name}",
            data=form_data,
            files={"file": (record_name, pdf_binary, "application/pdf")},
            write_timeout=60.0,
        )
        if result is None:
            return None
        if not result.get("success"):
            self.logger.error(f"❌ Docling service returned error for {record_name}: {result.get('error', 'Unknown error')}")
            return None

        return result["parse_result"]

    async def create_blocks(self, parse_result: str, page_number: int = None) -> Optional[BlocksContainer]:
        """
        Create blocks from parse result using the external Docling service (phase 2).

        Args:
            parse_result: Serialized parse result from parse_pdf
            page_number: Optional page number for page-specific processing

        Returns:
            BlocksContainer if successful, None if failed
        """
        result = await self._post_with_retry(
            "/create-blocks",
            "Creating blocks",
            json_body={"parse_result": parse_result, "page_number": page_number},
        )
        if result is None:
            return None
        if not result.get("success"):
            self.logger.error(f"❌ Docling service returned error: {result.get('error', 'Unknown error')}")
            return None

        return await asyncio.to_thread(self._parse_blocks_container, result["block_containers"])

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
