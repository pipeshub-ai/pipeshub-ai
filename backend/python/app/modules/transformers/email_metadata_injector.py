from logging import Logger

from app.models.blocks import Block, BlockType, DataFormat
from app.models.entities import RecordType
from app.modules.transformers.transformer import TransformContext, Transformer


class EmailMetadataInjector(Transformer):
    """
    Transformer that injects email metadata (Sender, Recipients, Subject)
    into the content block text so it gets indexed by the embedding model.
    """

    def __init__(self, logger: Logger) -> None:
        super().__init__()
        self.logger = logger

    async def apply(self, ctx: TransformContext) -> None:
        try:
            record = ctx.record

            # Optimization: Use Enum checks
            if record.record_type not in (RecordType.MAIL, RecordType.GROUP_MAIL):
                return

            subject = str(getattr(record, "subject", "") or "No Subject")
            from_addr = str(
                getattr(record, "from_address", None)
                or getattr(record, "from_email", None)
                or "Unknown Sender"
            )

            to_addrs = (
                getattr(record, "to_addresses", None)
                or getattr(record, "to_emails", None)
                or []
            )
            cc_addrs = (
                getattr(record, "cc_addresses", None)
                or getattr(record, "cc_emails", None)
                or []
            )

            to_addrs = [str(a) for a in to_addrs if a]
            cc_addrs = [str(a) for a in cc_addrs if a]

            header_lines = [
                f"Subject: {subject}",
                f"From: {from_addr}",
            ]

            if to_addrs:
                header_lines.append(f"To: {', '.join(to_addrs)}")

            if cc_addrs:
                header_lines.append(f"CC: {', '.join(cc_addrs)}")

            # Separator used for idempotency check
            separator = "\n\n--- Content ---\n\n"
            metadata_header = "\n".join(header_lines) + separator

            if not hasattr(record, "block_containers") or not record.block_containers:
                return

            blocks = record.block_containers.blocks
            if blocks is None:
                blocks = []
                record.block_containers.blocks = blocks

            target_block_index = -1
            for i, block in enumerate(blocks):
                if not block:
                    continue

                if hasattr(block, "type") and (
                    block.type in [BlockType.TEXT, "text"]
                ):
                    target_block_index = i
                    break

            if target_block_index >= 0:
                target_block = blocks[target_block_index]
                original_text = str(target_block.data or "")

                # Improved Idempotency Check
                if original_text.startswith("Subject: ") and separator in original_text:
                    return

                target_block.data = metadata_header + original_text
            else:
                try:
                    new_block = Block(
                        index=0,
                        type=BlockType.TEXT,
                        data=metadata_header,
                        format=DataFormat.TXT,
                    )

                    blocks.insert(0, new_block)

                    for i, b in enumerate(blocks):
                        if b:
                            b.index = i

                except Exception as e:
                    self.logger.error(
                        f"Failed to create new metadata block for record {ctx.record.id}: {e}",
                        exc_info=True,
                    )

        except Exception as e:
            self.logger.error(
                f"Failed to inject email metadata for record {ctx.record.id}: {e}",
                exc_info=True,
            )
