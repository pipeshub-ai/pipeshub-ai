import email.header
import email.utils
import re
from logging import Logger
from typing import Any, Dict, List, Tuple

from app.config.constants.arangodb import CollectionNames
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.models.entities import RecordType
from app.modules.transformers.transformer import TransformContext, Transformer


class EmailMetadataInjector(Transformer):
    """Inject email metadata as a separate block with atomic persistence."""

    # Constants
    SUBJECT_PREFIX = "Subject: "
    FROM_PREFIX = "From: "
    TO_PREFIX = "To: "
    CC_PREFIX = "CC: "
    SEPARATOR = "\n\n--- Content ---\n\n"
    NO_SUBJECT = "No Subject"
    UNKNOWN_SENDER = "Unknown Sender"
    METADATA_BLOCK_NAME = "Email Metadata"
    METADATA_VERSION = "v1"

    # Defaults
    DEFAULT_MAX_RECIPIENTS = 20
    DEFAULT_MAX_METADATA_CHARS = 2000

    def __init__(
        self, logger: Logger, arango_service: Any, config_service: Any = None
    ) -> None:
        super().__init__()
        self.logger = logger
        self.arango_service = arango_service
        self.config_service = config_service

        # Defaults - actual config loaded async in apply if needed
        self.max_recipients = self.DEFAULT_MAX_RECIPIENTS
        self.max_metadata_chars = self.DEFAULT_MAX_METADATA_CHARS

    def _decode_header(self, text: str) -> str:
        """Decode MIME encoded header words."""
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

    def _sanitize_text(self, text: str) -> str:
        """Remove control characters and normalize whitespace."""
        if not text:
            return ""
        # Remove C0 control chars except \n, \t, \r
        cleaned = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", text)
        # Normalize multiple spaces
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def _normalize_addresses(self, raw: Any) -> List[str]:
        """Parse and normalize email addresses with deduplication."""
        if not raw:
            return []

        if isinstance(raw, str):
            raw_list = [raw]
        elif isinstance(raw, list):
            raw_list = [str(item) for item in raw]
        else:
            raw_list = [str(raw)]

        pairs = email.utils.getaddresses(raw_list)

        # Order-preserving deduplication
        seen = set()
        results = []
        for name, addr in pairs:
            if not addr:
                continue

            decoded_name = self._decode_header(name)
            formatted = f"{decoded_name} <{addr}>" if decoded_name else addr

            if formatted not in seen:
                seen.add(formatted)
                results.append(formatted)

        return results

    def _build_metadata_header(
        self, subject: str, from_addr: str, to_addrs: List[str], cc_addrs: List[str]
    ) -> Tuple[str, bool]:
        """Build metadata header with truncation handling. Returns (content, truncated_flag)."""
        # Truncate recipients if needed
        truncated = False
        if len(to_addrs) > self.max_recipients:
            remaining = len(to_addrs) - self.max_recipients
            to_addrs = to_addrs[: self.max_recipients]
            to_addrs.append(f"... ({remaining} more)")
            truncated = True

        if len(cc_addrs) > self.max_recipients:
            remaining = len(cc_addrs) - self.max_recipients
            cc_addrs = cc_addrs[: self.max_recipients]
            cc_addrs.append(f"... ({remaining} more)")
            truncated = True

        # Build header lines
        header_lines = [
            f"{self.SUBJECT_PREFIX}{subject}",
            f"{self.FROM_PREFIX}{from_addr}",
            f"[EmailMetadata: {self.METADATA_VERSION}]",
        ]

        if to_addrs:
            header_lines.append(f"{self.TO_PREFIX}{', '.join(to_addrs)}")
        if cc_addrs:
            header_lines.append(f"{self.CC_PREFIX}{', '.join(cc_addrs)}")

        metadata = "\n".join(header_lines) + self.SEPARATOR

        # Apply character limit
        if len(metadata) > self.max_metadata_chars:
            original_len = len(metadata)
            metadata = metadata[: self.max_metadata_chars] + "...\n"
            self.logger.info(
                "Truncated metadata header",
                extra={
                    "original_len": original_len,
                    "truncated_len": len(metadata),
                    "event": "metadata_truncated",
                },
            )

        return self._sanitize_text(metadata), truncated

    async def _persist_metadata_atomic(
        self, record_key: str, metadata_block: Dict[str, Any]
    ) -> bool:
        """
        Atomically append metadata block and set flag in single transaction.
        Returns True if update was performed, False if already injected.
        """
        try:
            query = """
            LET key = @key
            LET new_block = @new_block

            FOR doc IN @@records
              FILTER doc._key == key
              FILTER doc.is_metadata_injected != true

              // Ensure block_containers structure exists
              LET containers = doc.block_containers ?: {}
              LET blocks = containers.blocks ?: []

              // Check if metadata block already exists (idempotency safety check)
              LET has_metadata = (
                FOR b IN blocks
                  FILTER b.name == @block_name
                  LIMIT 1
                  RETURN true
              )

              FILTER LENGTH(has_metadata) == 0

              // Prepend new block (insert at position 0)
              LET updated_blocks = (
                LET all_blocks = APPEND([new_block], blocks, false)
                FOR i, b IN all_blocks
                  LET updated_b = MERGE(b, { index: i })
                  RETURN updated_b
              )

              UPDATE doc WITH {
                is_metadata_injected: true,
                block_containers: MERGE(containers, { blocks: updated_blocks })
              } IN @@records

              RETURN NEW._key
            """

            bind_vars = {
                "@records": CollectionNames.RECORDS.value,
                "key": record_key,
                "new_block": metadata_block,
                "block_name": self.METADATA_BLOCK_NAME,
            }

            cursor = self.arango_service.db.aql.execute(query, bind_vars=bind_vars)

            # Check if we got a result
            try:
                result = next(cursor)
                if result:
                    self.logger.debug(
                        "Atomic metadata persistence successful",
                        extra={"record_key": result},
                    )
                    return True
            except StopIteration:
                pass

            return False

        except Exception as e:
            self.logger.error(
                "Failed atomic metadata persistence",
                extra={"record_key": record_key, "error": str(e)},
                exc_info=True,
            )
            return False

    async def apply(self, ctx: TransformContext) -> None:
        """Main transformation logic with atomic persistence."""
        if not ctx or not ctx.record:
            return

        record = ctx.record
        record_key = getattr(record, "id", None)

        if not record_key:
            self.logger.warning("Record has no ID", extra={"event": "no_record_id"})
            return

        try:
            # Load config async if needed
            if self.config_service:
                try:
                    self.max_recipients = int(
                        await self.config_service.get_config(
                            "email.max_recipients", self.DEFAULT_MAX_RECIPIENTS
                        )
                    )
                    self.max_metadata_chars = int(
                        await self.config_service.get_config(
                            "email.max_metadata_chars", self.DEFAULT_MAX_METADATA_CHARS
                        )
                    )
                except Exception:
                    # Fallback silently or log debug
                    pass

            # 1. Record type check
            if record.record_type not in (RecordType.MAIL, RecordType.GROUP_MAIL):
                return

            # 2. Quick memory flag check
            if getattr(record, "is_metadata_injected", False):
                self.logger.debug(
                    "Skipping - in-memory flag set",
                    extra={"record_id": record_key, "event": "skipped_mem_flag"},
                )
                return

            # 3. Extract and prepare metadata
            subject = self._decode_header(
                str(getattr(record, "subject", "") or self.NO_SUBJECT)
            )

            from_raw = (
                getattr(record, "from_address", None)
                or getattr(record, "from_email", None)
                or self.UNKNOWN_SENDER
            )
            from_addrs = self._normalize_addresses(from_raw)
            from_addr = from_addrs[0] if from_addrs else str(from_raw)

            to_addrs = self._normalize_addresses(
                getattr(record, "to_addresses", []) or getattr(record, "to_emails", [])
            )
            cc_addrs = self._normalize_addresses(
                getattr(record, "cc_addresses", []) or getattr(record, "cc_emails", [])
            )

            # 4. Build metadata content
            metadata_content, truncated = self._build_metadata_header(
                subject, from_addr, to_addrs, cc_addrs
            )

            # 5. Create metadata block dict for DB
            metadata_block = {
                "index": 0,
                "type": BlockType.TEXT.value,
                "data": metadata_content,
                "format": DataFormat.TXT.value,
                "name": self.METADATA_BLOCK_NAME,
                "metadata_version": self.METADATA_VERSION,
            }

            # 6. Attempt atomic persistence
            success = await self._persist_metadata_atomic(record_key, metadata_block)

            if success:
                # Update local record state
                record.is_metadata_injected = True

                # Insert block locally (for subsequent transformers)
                if (
                    not hasattr(record, "block_containers")
                    or not record.block_containers
                ):
                    record.block_containers = BlocksContainer(blocks=[])

                new_block = Block(
                    index=0,
                    type=BlockType.TEXT,
                    data=metadata_content,
                    format=DataFormat.TXT,
                    name=self.METADATA_BLOCK_NAME,
                )

                if record.block_containers.blocks is not None:
                    # Adjust existing block indices
                    for block in record.block_containers.blocks:
                        block.index += 1
                    record.block_containers.blocks.insert(0, new_block)
                else:
                    record.block_containers.blocks = [new_block]

                self.logger.info(
                    "Successfully injected email metadata",
                    extra={
                        "record_id": record_key,
                        "subject_length": len(subject),
                        "to_count": len(to_addrs),
                        "cc_count": len(cc_addrs),
                        "truncated": truncated,
                        "event": "metadata_injected",
                    },
                )

            else:
                # Another worker already injected or record not found
                record.is_metadata_injected = True
                self.logger.debug(
                    "Metadata already injected or race condition",
                    extra={"record_id": record_key, "event": "skipped_atomic"},
                )

        except Exception as e:
            self.logger.error(
                "Failed to inject email metadata",
                extra={
                    "record_id": record_key,
                    "error": str(e),
                    "event": "injection_failed",
                },
                exc_info=True,
            )
