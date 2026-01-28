import email.header
import email.utils
import re
from logging import Logger
from typing import Any, List

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import Block, BlockType, DataFormat
from app.models.entities import RecordType
from app.modules.transformers.transformer import TransformContext, Transformer


class EmailMetadataInjector(Transformer):
    """
    Transformer that injects email metadata (Sender, Recipients, Subject)
    into the content block text so it gets indexed by the embedding model.

    Dependencies:
    - Relies on 'Block.name' field to identify metadata blocks.
    - Requires DB access for atomic idempotency checks.
    """

    SUBJECT_PREFIX = "Subject: "
    FROM_PREFIX = "From: "
    TO_PREFIX = "To: "
    CC_PREFIX = "CC: "
    SEPARATOR = "\n\n--- Content ---\n\n"
    NO_SUBJECT = "No Subject"
    UNKNOWN_SENDER = "Unknown Sender"
    METADATA_BLOCK_NAME = "Email Metadata"

    def __init__(
        self, logger: Logger, arango_service: Any, config_service: Any
    ) -> None:
        super().__init__()
        self.logger = logger
        self.arango_service = arango_service
        self.config_service = config_service

        # Default config; ideally fetch from config_service
        self.max_recipients = 20
        self.max_metadata_chars = 2000

    def _decode_header(self, text: str) -> str:
        """Decode MIME encoded header words and normalize whitespace."""
        if not text:
            return ""
        try:
            decoded_fragments = email.header.decode_header(text)
            decoded_str = ""
            for bytes_chunk, encoding in decoded_fragments:
                if isinstance(bytes_chunk, bytes):
                    if encoding:
                        try:
                            decoded_str += bytes_chunk.decode(
                                encoding, errors="replace"
                            )
                        except LookupError:
                            decoded_str += bytes_chunk.decode("utf-8", errors="replace")
                    else:
                        decoded_str += bytes_chunk.decode("utf-8", errors="replace")
                else:
                    decoded_str += str(bytes_chunk)
            return re.sub(r"\s+", " ", decoded_str).strip()
        except Exception:
            return text

    def _normalize_addresses(self, raw: Any) -> List[str]:
        """Parse and normalize email addresses using email.utils."""
        if not raw:
            return []

        if isinstance(raw, str):
            raw_list = [raw]
        elif isinstance(raw, list):
            raw_list = [str(item) for item in raw]
        else:
            raw_list = [str(raw)]

        pairs = email.utils.getaddresses(raw_list)

        results = []
        for name, addr in pairs:
            if not addr:
                continue

            decoded_name = self._decode_header(name)

            if decoded_name:
                results.append(f"{decoded_name} <{addr}>")
            else:
                results.append(addr)

        return results

    def _safe_str(self, data: Any) -> str:
        """Safely convert data to string, handling bytes."""
        if data is None:
            return ""
        if isinstance(data, str):
            return data
        if isinstance(data, bytes):
            try:
                return data.decode("utf-8", errors="replace")
            except Exception:
                return str(data)
        return str(data)

    async def _mark_metadata_injected_db(self, record_id: str) -> bool:
        """
        Atomically sets is_metadata_injected=true in DB if it is currently false.
        Returns True if the update was performed (lock acquired/persisted).
        Returns False if the record was already marked (race condition lost).
        """
        try:
            query = """
            FOR doc IN @@records
                FILTER doc._key == @key
                FILTER doc.is_metadata_injected != true
                
                UPDATE doc WITH { is_metadata_injected: true } IN @@records
                RETURN 1
            """
            bind_vars = {"@records": CollectionNames.RECORDS.value, "key": record_id}
            cursor = self.arango_service.db.aql.execute(query, bind_vars=bind_vars)

            if len(list(cursor)) > 0:
                return True
            return False

        except Exception as e:
            self.logger.warning(
                "Failed to persist metadata flag",
                extra={"record_id": record_id, "error": str(e)},
            )
            return False

    async def apply(self, ctx: TransformContext) -> None:
        record_id = "unknown"
        try:
            if not ctx or not ctx.record:
                return

            record = ctx.record
            record_id = getattr(record, "id", "unknown")

            if record.record_type not in (RecordType.MAIL, RecordType.GROUP_MAIL):
                return

            # 1. In-memory flag check (First line of defense)
            if getattr(record, "is_metadata_injected", False):
                self.logger.info(
                    "Skipping metadata injection (in-memory flag)",
                    extra={
                        "record_id": record_id,
                        "event": "skipped_already_injected_mem",
                    },
                )
                return

            # 2. Scan ALL blocks for Idempotency
            if record.block_containers and record.block_containers.blocks:
                for block in record.block_containers.blocks:
                    if getattr(block, "name", "") == self.METADATA_BLOCK_NAME:
                        record.is_metadata_injected = True
                        self.logger.info(
                            "Skipping metadata injection (block name found)",
                            extra={
                                "record_id": record_id,
                                "event": "skipped_already_injected_block",
                            },
                        )
                        return

                    block_data = self._safe_str(getattr(block, "data", ""))
                    if self.SEPARATOR in block_data and block_data.lstrip().startswith(
                        self.SUBJECT_PREFIX
                    ):
                        record.is_metadata_injected = True
                        self.logger.info(
                            "Skipping metadata injection (content signature found)",
                            extra={
                                "record_id": record_id,
                                "event": "skipped_already_injected_sig",
                            },
                        )
                        return

            # Prepare Metadata
            subject_raw = str(getattr(record, "subject", "") or self.NO_SUBJECT)
            subject = self._decode_header(subject_raw)

            from_raw = (
                getattr(record, "from_address", None)
                or getattr(record, "from_email", None)
                or self.UNKNOWN_SENDER
            )
            from_addr = (
                self._normalize_addresses(from_raw)[0]
                if self._normalize_addresses(from_raw)
                else from_raw
            )

            to_addrs = self._normalize_addresses(
                getattr(record, "to_addresses", []) or getattr(record, "to_emails", [])
            )
            cc_addrs = self._normalize_addresses(
                getattr(record, "cc_addresses", []) or getattr(record, "cc_emails", [])
            )

            # Metrics for truncation
            truncated_recipients = False
            if len(to_addrs) > self.max_recipients:
                remaining = len(to_addrs) - self.max_recipients
                to_addrs = to_addrs[: self.max_recipients]
                to_addrs.append(f"... ({remaining} more)")
                truncated_recipients = True

            if len(cc_addrs) > self.max_recipients:
                remaining = len(cc_addrs) - self.max_recipients
                cc_addrs = cc_addrs[: self.max_recipients]
                cc_addrs.append(f"... ({remaining} more)")
                truncated_recipients = True

            header_lines = [
                f"{self.SUBJECT_PREFIX}{subject}",
                f"{self.FROM_PREFIX}{from_addr}",
            ]

            if to_addrs:
                header_lines.append(f"{self.TO_PREFIX}{', '.join(to_addrs)}")

            if cc_addrs:
                header_lines.append(f"{self.CC_PREFIX}{', '.join(cc_addrs)}")

            metadata_header = "\n".join(header_lines) + self.SEPARATOR

            # Global Token Cap
            original_len = len(metadata_header)
            if len(metadata_header) > self.max_metadata_chars:
                metadata_header = metadata_header[: self.max_metadata_chars] + "...\n"
                self.logger.info(
                    "Truncated metadata header",
                    extra={
                        "record_id": record_id,
                        "original_len": original_len,
                        "new_len": len(metadata_header),
                        "event": "truncated_metadata",
                    },
                )

            # Insert Block
            try:
                if (
                    not hasattr(record, "block_containers")
                    or not record.block_containers
                ):
                    return

                blocks = record.block_containers.blocks
                if blocks is None:
                    blocks = []
                    record.block_containers.blocks = blocks

                new_block = Block(
                    index=0,
                    type=BlockType.TEXT,
                    data=metadata_header,
                    format=DataFormat.TXT,
                    name=self.METADATA_BLOCK_NAME,
                )

                # Insert locally
                blocks.insert(0, new_block)
                for i, b in enumerate(blocks):
                    if b:
                        b.index = i

                # 3. Persistence & Concurrency
                success = await self._mark_metadata_injected_db(record_id)

                if success:
                    record.is_metadata_injected = True
                    self.logger.info(
                        "Successfully injected email metadata",
                        extra={
                            "record_id": record_id,
                            "subject_len": len(subject),
                            "to_count": len(to_addrs),
                            "truncated_recipients": truncated_recipients,
                            "event": "injected_success",
                        },
                    )
                else:
                    # Race lost
                    self.logger.warning(
                        "Race condition lost - reverting injection",
                        extra={"record_id": record_id, "event": "reverted_race_lost"},
                    )
                    blocks.pop(0)
                    for i, b in enumerate(blocks):
                        if b:
                            b.index = i
                    record.is_metadata_injected = True

            except Exception as e:
                self.logger.error(
                    "Failed to create/insert metadata block",
                    extra={
                        "record_id": record_id,
                        "error": str(e),
                        "event": "failed_block_insertion",
                    },
                    exc_info=True,
                )

        except Exception as e:
            self.logger.error(
                "Failed to inject email metadata (global error)",
                extra={
                    "record_id": record_id,
                    "error": str(e),
                    "event": "failed_global",
                },
                exc_info=True,
            )
