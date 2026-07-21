import json
import logging
from typing import Any, List, Tuple
from app.config.configuration_service import ConfigurationService
from app.models.blocks import Block, BlockContainerIndex, BlockGroup, BlockGroupChildren, BlockType, BlocksContainer, DataFormat, GroupType
from app.modules.parsers.html_parser.html_parser import HTMLParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.services.parsing.interface import ParseErrorCode, ParseError, ParseResult


class BlocksParser:
    def __init__(self, logger: logging.Logger, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.config_service = config_service
    
    def _build_updated_blocks_container(
        self,
        block_containers: BlocksContainer,
        block_groups_with_index: List[BlockGroup],
        block_groups_without_index: List[BlockGroup],
        processing_results: dict[int, Tuple[List[BlockGroup], List[Block]]],
        index_shift_map: dict[int, int],
    ) -> BlocksContainer:
        """
        Build the final BlocksContainer with updated indices.

        Handles both:
        - BlockGroups with requires_processing=True: blocks from docling processing
        - BlockGroups with requires_processing=False: existing blocks from connector

        All blocks are assigned sequential indices in BlockGroup order.

        Args:
            block_containers: Original BlocksContainer
            block_groups_with_index: Block groups with valid indices
            block_groups_without_index: Block groups without indices
            processing_results: Map of parent_index -> (new_block_groups, new_blocks)
            index_shift_map: Map of original_index to shift amount

        Returns:
            New BlocksContainer with processed blocks merged in
        """
        new_block_groups: List[BlockGroup] = []
        new_blocks: List[Block] = []
        processed_indices = set(processing_results.keys())

        # Group existing blocks by their original parent_index
        # (before any shifting is applied to BlockGroup indices)
        existing_blocks_by_parent: dict[int, List[Block]] = {}
        for block in block_containers.blocks:
            parent_idx = block.parent_index
            if parent_idx is not None:
                if parent_idx not in existing_blocks_by_parent:
                    existing_blocks_by_parent[parent_idx] = []
                existing_blocks_by_parent[parent_idx].append(block)

        # Sort blocks within each parent group by their original index to maintain relative order
        for parent_idx in existing_blocks_by_parent:
            existing_blocks_by_parent[parent_idx].sort(
                key=lambda b: b.index if b.index is not None else float('inf')
            )

        # Track current block index for sequential assignment
        current_block_index = 0

        # Build new block_groups list and assign block indices in BlockGroup order
        for bg in block_groups_with_index:
            original_index = bg.index
            shift_amount = index_shift_map[original_index]
            final_index = original_index + shift_amount

            # Update block_group's index
            bg.index = final_index

            # Update parent_index if it references a shifted block_group
            if bg.parent_index is not None and bg.parent_index in index_shift_map:
                bg.parent_index += index_shift_map[bg.parent_index]

            # Update children.block_group_ranges references
            if bg.children and bg.children.block_group_ranges:
                shifted_indices = []
                for range_obj in bg.children.block_group_ranges:
                    for idx in range(range_obj.start, range_obj.end + 1):
                        if idx in index_shift_map:
                            shifted_indices.append(idx + index_shift_map[idx])
                        else:
                            shifted_indices.append(idx)
                # Reconstruct ranges from shifted indices
                bg.children.block_group_ranges = BlockGroupChildren.from_indices(
                    block_group_indices=shifted_indices
                ).block_group_ranges

            # Add the block_group to the result
            new_block_groups.append(bg)

            # Handle blocks for this BlockGroup
            if original_index in processed_indices:
                # Case 1: BlockGroup was processed by docling - use new blocks
                bg.requires_processing = False

                # Get processing results
                new_block_groups_list, new_blocks_list = processing_results[original_index]
                insertion_index = final_index + 1

                # Initialize children if needed
                if bg.children is None:
                    bg.children = BlockGroupChildren()

                # Clear existing block_ranges since we're replacing with processed blocks
                bg.children.block_ranges = []

                # First, assign indices to all blocks (docling gives proper order)
                # This ensures we know the final indices before updating nested block_group ranges
                block_start_index = current_block_index
                for new_block in new_blocks_list:
                    # Assign sequential block index
                    new_block.index = current_block_index

                    # Set parent_index
                    if new_block.parent_index is None:
                        new_block.parent_index = final_index
                    else:
                        # If parent_index exists, it's a relative index from docling
                        new_block.parent_index = new_block.parent_index + insertion_index

                    new_blocks.append(new_block)

                    # Add blocks that directly belong to the parent BlockGroup
                    if new_block.parent_index == final_index:
                        bg.children.add_block_index(new_block.index)

                    current_block_index += 1

                # Now assign indices to new block_groups and update their ranges
                # (ranges can now reference the correctly assigned block indices)
                for i, new_bg in enumerate(new_block_groups_list):
                    new_bg.index = insertion_index + i

                    # Set parent_index to parent's final index if not set
                    if new_bg.parent_index is None:
                        new_bg.parent_index = final_index
                    else:
                        # If parent_index exists, it's a relative index from docling
                        new_bg.parent_index = new_bg.parent_index + insertion_index

                    # Update children indices in the new block_group
                    # Since blocks are already assigned, shift ranges by block_start_index
                    if new_bg.children:
                        # Shift block_ranges (docling returns ranges relative to its output starting at 0)
                        for range_obj in new_bg.children.block_ranges:
                            range_obj.start += block_start_index
                            range_obj.end += block_start_index

                        # Shift block_group_ranges
                        for range_obj in new_bg.children.block_group_ranges:
                            range_obj.start += insertion_index
                            range_obj.end += insertion_index

                    new_block_groups.append(new_bg)

                    # Add to parent's children
                    bg.children.add_block_group_index(new_bg.index)

            elif original_index in existing_blocks_by_parent:
                # Case 2: BlockGroup has existing blocks from connector - reassign indices
                existing_blocks = existing_blocks_by_parent[original_index]

                # Initialize children if needed
                if bg.children is None:
                    bg.children = BlockGroupChildren()

                # Clear and rebuild block_ranges with new indices
                bg.children.block_ranges = []

                for block in existing_blocks:
                    # Update parent_index to the shifted BlockGroup index
                    block.parent_index = final_index

                    # Assign new sequential block index
                    block.index = current_block_index
                    new_blocks.append(block)

                    # Add to parent's children
                    bg.children.add_block_index(block.index)

                    current_block_index += 1

        # Append block_groups with None index at end
        new_block_groups.extend(block_groups_without_index)

        # Sort block_groups by index to ensure list position matches index value
        sorted_block_groups = sorted(
            new_block_groups,
            key=lambda bg: bg.index if bg.index is not None else float('inf')
        )

        # Sort blocks by index to ensure list position matches index value
        sorted_blocks = sorted(
            new_blocks,
            key=lambda b: b.index if b.index is not None else float('inf')
        )

        # Build final BlocksContainer
        return BlocksContainer(
            block_groups=sorted_block_groups,
            blocks=sorted_blocks
        )

    def _separate_block_groups_by_index(
        self, block_groups: List[BlockGroup]
    ) -> Tuple[List[BlockGroup], List[BlockGroup]]:
        """
        Separate block groups into those with valid index and those without.

        Args:
            block_groups: List of block groups to separate

        Returns:
            Tuple of (block_groups_with_index, block_groups_without_index)
        """
        block_groups_with_index: List[BlockGroup] = []
        block_groups_without_index: List[BlockGroup] = []

        for bg in block_groups:
            if bg.index is not None:
                block_groups_with_index.append(bg)
            else:
                block_groups_without_index.append(bg)

        return block_groups_with_index, block_groups_without_index
    
    async def _process_blockgroup_images(
        self, markdown_data: str
    ) -> Tuple[str, dict[str, str]]:
        """
        Extract images from markdown and convert URLs to base64.

        Args:
            markdown_data: Markdown content to process

        Returns:
            Tuple of (modified_markdown, caption_map) where caption_map maps alt text to base64 URIs
        """
        caption_map: dict[str, str] = {}
        modified_markdown = markdown_data

        image_parser = ImageParser(self.logger)
        md_parser = MarkdownParser(logger=self.logger, config_service=self.config_service)

        if md_parser and image_parser:
            modified_markdown, images = md_parser.extract_and_replace_images(markdown_data)

            if images:
                # Collect all image URLs
                urls_to_convert = [image["url"] for image in images]

                # Convert URLs to base64
                base64_urls = await image_parser.urls_to_base64(urls_to_convert)

                # Create caption map with base64 URLs
                for i, image in enumerate(images):
                    if base64_urls[i]:
                        caption_map[image["new_alt_text"]] = base64_urls[i]

        return modified_markdown, caption_map

    async def _process_single_blockgroup(
        self,
        block_group: BlockGroup,
        record_name: str,
        md_parser: MarkdownParser,
    ) -> Tuple[List[BlockGroup], List[Block]]:
        """
        Process a single block group's markdown into blocks.

        Args:
            block_group: Block group to process
            record_name: Name of the record (for filename generation)
            md_parser: Markdown parser instance

        Returns:
            Tuple of (new_block_groups, new_blocks) from processing

        Raises:
            ValueError: If block group has no valid markdown data
        """
        # Extract markdown data from BlockGroup
        markdown_data = block_group.data
        if not markdown_data or not isinstance(markdown_data, str):
            raise ValueError(
                f"BlockGroup {block_group.index} has no valid markdown data"
            )

        # Extract and replace images from markdown, then convert URLs to base64
        modified_markdown, caption_map = await self._process_blockgroup_images(
            markdown_data
        )

        processed_blocks_container = await md_parser.parse_to_blocks(
            modified_markdown,
            caption_map=caption_map or None,
            name=block_group.name or record_name,
        )

        return processed_blocks_container.block_groups, processed_blocks_container.blocks

    async def _process_single_blockgroup_html(
        self,
        block_group: BlockGroup,
        record_name: str,
        html_parser: HTMLParser,
    ) -> Tuple[List[BlockGroup], List[Block]]:
        """
        Process a single block group's HTML data into blocks.

        Mirrors ``_process_single_blockgroup`` but uses the HTML parser.
        Images should already be base64-inlined by the connector; any remaining
        remote URLs are fetched as a safety net.
        """
        html_data = block_group.data
        if not html_data or not isinstance(html_data, str):
            raise ValueError(
                f"BlockGroup {block_group.index} has no valid HTML data"
            )

        html_content = html_parser.clean_html(html_data)

        caption_map: dict[str, str] = {}
        modified_html, images = html_parser.extract_and_replace_images(html_content)

        if images:
            image_parser = ImageParser(self.logger)
            urls_to_convert = [image["url"] for image in images]
            base64_urls = await image_parser.urls_to_base64(urls_to_convert)
            for i, image in enumerate(images):
                if base64_urls[i]:
                    caption_map[image["new_alt_text"]] = base64_urls[i]

        processed_blocks_container = await html_parser.parse_to_blocks(
            modified_html,
            caption_map=caption_map or None,
            name=block_group.name or record_name,
        )

        return processed_blocks_container.block_groups, processed_blocks_container.blocks

    def _calculate_index_shift_map(
        self,
        block_groups_with_index: List[BlockGroup],
        processing_results: dict[int, Tuple[List[BlockGroup], List[Block]]]
    ) -> dict[int, int]:
        """
        Calculate index shift mappings for block groups.

        Builds a map of original_index -> cumulative_shift_amount where
        cumulative_shift = sum of new_block_groups from all parents with index < original_index.

        Args:
            block_groups_with_index: List of block groups with valid indices
            processing_results: Map of parent_index -> (new_block_groups, new_blocks)

        Returns:
            Dictionary mapping original_index to shift amount
        """
        index_shift_map: dict[int, int] = {}
        cumulative_shift = 0

        for bg in block_groups_with_index:
            original_index = bg.index
            index_shift_map[original_index] = cumulative_shift

            # If this block_group was processed, add its new block_groups to the shift
            if original_index in processing_results:
                num_new_block_groups = len(processing_results[original_index][0])
                cumulative_shift += num_new_block_groups

        return index_shift_map


    async def _process_blockgroups(
        self, block_containers: BlocksContainer, record_name: str
    ) -> BlocksContainer:
        """
        Process BlockGroups with requires_processing=True via markdown or HTML parser.

        Uses a functional approach:
        1. Process all BlockGroups that need processing, collecting results
        2. Calculate index mappings upfront
        3. Build new BlocksContainer in a single pass

        Args:
            block_containers: BlocksContainer to process
            record_name: Name of the record (for parser context)

        Returns:
            BlocksContainer with processed blocks merged in
        """
        if not block_containers.block_groups:
            return block_containers

        # Separate block_groups with valid index from those with None index
        block_groups_with_index, block_groups_without_index = self._separate_block_groups_by_index(
            block_containers.block_groups
        )

        # Filter BlockGroups that need processing (already in sequence from connector)
        block_groups_to_process = [
            bg for bg in block_groups_with_index
            if bg.requires_processing and bg.data
        ]

        if not block_groups_to_process:
            return block_containers

        # ========== PHASE 1: Process all BlockGroups and collect results ==========
        # Map: parent_index -> (new_block_groups, new_blocks)
        processing_results: dict[int, Tuple[List[BlockGroup], List[Block]]] = {}

        md_parser = MarkdownParser(logger=self.logger, config_service=self.config_service)
        html_parser = HTMLParser(logger=self.logger, config_service=self.config_service)

        for block_group in block_groups_to_process:
            try:
                if block_group.format == DataFormat.HTML:
                    new_block_groups, new_blocks = await self._process_single_blockgroup_html(
                        block_group, record_name, html_parser
                    )
                else:
                    new_block_groups, new_blocks = await self._process_single_blockgroup(
                        block_group, record_name, md_parser
                    )

                # Store results for later merging
                processing_results[block_group.index] = (new_block_groups, new_blocks)

            except Exception as e:
                # Stop processing if any BlockGroup fails
                raise

        if not processing_results:
            return block_containers

        # ========== PHASE 2: Calculate index mappings upfront ==========
        index_shift_map = self._calculate_index_shift_map(
            block_groups_with_index, processing_results
        )

        # ========== PHASE 3: Build new BlocksContainer in a single pass ==========
        result = self._build_updated_blocks_container(
            block_containers,
            block_groups_with_index,
            block_groups_without_index,
            processing_results,
            index_shift_map,
        )

        return result

    async def _enhance_tables_with_llm(self, block_containers: BlocksContainer) -> None:
        """
        Enhance TABLE BlockGroups with LLM-generated summaries and row descriptions.

        This method processes all TABLE BlockGroups in the container:
        - Generates table summary and enhanced column headers using LLM
        - Generates natural language descriptions for each row
        - Updates BlockGroup and Block data with enhanced content

        Args:
            block_containers: The BlocksContainer to enhance in-place
        """
        from app.utils.indexing_helpers import (
            get_rows_text,
            get_table_summary_n_headers,
        )

        # Find all TABLE BlockGroups
        table_groups = [
            bg for bg in block_containers.block_groups
            if bg.type == GroupType.TABLE
        ]

        if not table_groups:
            return

        for table_group in table_groups:
            # Get table markdown from data
            table_markdown = table_group.data.get("table_markdown") if table_group.data else None
            if not table_markdown:
                continue

            # Get LLM-enhanced summary and column headers
            response = await get_table_summary_n_headers(self.config_service, table_markdown)

            if response:
                table_summary = response.summary or ""
                column_headers = response.headers or []

                # Update BlockGroup with enhanced data
                table_group.description = table_summary
                if table_group.data is None:
                    table_group.data = {}
                table_group.data["table_summary"] = table_summary
                table_group.data["column_headers"] = column_headers

                # Update TableMetadata if column headers are available
                if column_headers and table_group.table_metadata:
                    table_group.table_metadata.column_names = column_headers

                # Get all child row blocks for this table
                row_blocks = []
                row_dicts = []

                if table_group.children:
                    # Handle new BlockGroupChildren format (range-based)
                    if isinstance(table_group.children, BlockGroupChildren):
                        # Iterate over block ranges and expand to individual indices
                        for range_obj in table_group.children.block_ranges:
                            for block_index in range(range_obj.start, range_obj.end + 1):
                                if 0 <= block_index < len(block_containers.blocks):
                                    block = block_containers.blocks[block_index]
                                    if block.type == BlockType.TABLE_ROW:
                                        row_blocks.append(block)
                                        # Extract row dict from block data
                                        if block.data and "cells" in block.data:
                                            # Create row dict mapping column headers to cell values
                                            cells = block.data["cells"]
                                            if isinstance(cells, list) and column_headers:
                                                row_dict = {
                                                    col: cells[i] if i < len(cells) else ""
                                                    for i, col in enumerate(column_headers)
                                                }
                                                row_dicts.append(row_dict)
                                            else:
                                                row_dicts.append({})
                    # Handle old format (list of BlockContainerIndex) for backward compatibility
                    elif isinstance(table_group.children, list):
                        for child_idx in table_group.children:
                            if isinstance(child_idx, BlockContainerIndex) and child_idx.block_index is not None:
                                block_index = child_idx.block_index
                                if 0 <= block_index < len(block_containers.blocks):
                                    block = block_containers.blocks[block_index]
                                    if block.type == BlockType.TABLE_ROW:
                                        row_blocks.append(block)
                                        # Extract row dict from block data
                                        if block.data and "cells" in block.data:
                                            # Create row dict mapping column headers to cell values
                                            cells = block.data["cells"]
                                            if isinstance(cells, list) and column_headers:
                                                row_dict = {
                                                    col: cells[i] if i < len(cells) else ""
                                                    for i, col in enumerate(column_headers)
                                                }
                                                row_dicts.append(row_dict)
                                            else:
                                                row_dicts.append({})

                # Generate LLM row descriptions (skip header rows)
                # Filter out header rows using is_header flag from table_row_metadata
                non_header_row_dicts = []
                non_header_row_indices = []  # Track original indices for updating blocks

                for i, (row_dict, row_block) in enumerate(zip(row_dicts, row_blocks)):
                    # Check if this row is a header using the is_header flag from table_row_metadata
                    is_header = (
                        row_block
                        and row_block.table_row_metadata
                        and row_block.table_row_metadata.is_header
                    )

                    if not is_header:
                        non_header_row_dicts.append(row_dict)
                        non_header_row_indices.append(i)

                if non_header_row_dicts:
                    # get_rows_text skips row 0 when column_headers is provided
                    cols = column_headers or []
                    grid = []
                    if cols:
                        grid.append([{"text": col} for col in cols])
                    for row_dict in non_header_row_dicts:
                        grid_row = [{"text": row_dict.get(col, "")} for col in cols]
                        grid.append(grid_row)
                    table_data = {"grid": grid}
                    row_descriptions, _ = await get_rows_text(
                        self.config_service, table_data, table_summary, column_headers
                    )

                    # Update row blocks with LLM descriptions (only non-header rows)
                    for description_idx, original_idx in enumerate(non_header_row_indices):
                        if description_idx < len(row_descriptions) and original_idx < len(row_blocks):
                            row_block = row_blocks[original_idx]
                            if row_block.data:
                                row_block.data["row_natural_language_text"] = row_descriptions[description_idx]

    async def parse(self, content: bytes, record_name: str, config: dict[str, Any] | None = None) -> ParseResult:
        if isinstance(content, bytes):
            content = content.decode('utf-8')

        if isinstance(content, str):
            blocks_dict = json.loads(content)
        elif isinstance(content, dict):
            blocks_dict = content
        else:
            raise ParseError(ParseErrorCode.INVALID_INPUT, "Invalid content type", {"content_type": type(content).__name__})

        # Convert dict to BlocksContainer
        block_containers = BlocksContainer(**blocks_dict)

        # Process BlockGroups with requires_processing=True via markdown/HTML parser
        block_containers = await self._process_blockgroups(
            block_containers, record_name
        )

        # Enhance TABLE BlockGroups with LLM summaries and row descriptions
        await self._enhance_tables_with_llm(block_containers)

        return ParseResult(
            block_container=block_containers,
            metadata={"record_name": record_name},
        )