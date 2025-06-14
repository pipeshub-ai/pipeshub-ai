import base64
import logging
import os
from datetime import datetime
from urllib.parse import urlparse

import aiohttp


class NotionRouterService:
    """Service class for interacting with Notion API."""

    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, integration_secret: str, org_id: str, logger=None, arango_service=None):
        """Initialize Notion service with integration secret."""
        self.integration_secret = integration_secret
        self.org_id = org_id
        self.logger = logger or logging.getLogger(__name__)
        self.arango_service = arango_service
        self.headers = {
            "Authorization": f"Bearer {integration_secret}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        }

    async def fetch_blocks_of_page(self, page_id):
        self.logger.info(f"fetching blocks for page {page_id}")
        processed_blocks = []

        try:
            blocks = await self._fetch_block_children(page_id)
            self.logger.info(f"blocks: {blocks}")

            for block in blocks:
                block_id = block.get("id")
                block_type = block.get("type")
                block_created_time = block.get("created_time")
                block_last_edited_time = block.get("last_edited_time")

                if block_type in ["child_page", "child_database", "unsupported"]:
                    continue

                block_content = block.get(block_type, {})

                # Handle file blocks - return existing record info
                if block_type == "file":
                    self.logger.info("got a file record")
                    file_info = await self._get_file_info(block_content, block_type, block_id)
                    if file_info:
                        processed_blocks.append({
                            "block_id": block_id,
                            "block_type": block_type,
                            "record_id": file_info["record_id"],
                            "file_url": file_info["file_url"],
                            "name": file_info["name"],
                            "mimetype": file_info["mimetype"],
                            "created_time": block_created_time,
                            "last_edited_time": block_last_edited_time
                        })

                # Handle media blocks (video, image, gif) - return existing record info
                elif block_type in ["video", "image", "gif"]:
                    self.logger.info(f"got a {block_type} record - treating as file")
                    file_info = await self._get_file_info(block_content, block_type, block_id)
                    if file_info:
                        processed_blocks.append({
                            "block_id": block_id,
                            "block_type": block_type,
                            "record_id": file_info["record_id"],
                            "file_url": file_info["file_url"],
                            "name": file_info["name"],
                            "mimetype": file_info["mimetype"],
                            "created_time": block_created_time,
                            "last_edited_time": block_last_edited_time
                        })

                # Handle audio blocks - return existing record info
                elif block_type == "audio":
                    self.logger.info("got an audio record")
                    file_info = await self._get_file_info(block_content, block_type, block_id)
                    if file_info:
                        processed_blocks.append({
                            "block_id": block_id,
                            "block_type": block_type,
                            "record_id": file_info["record_id"],
                            "file_url": file_info["file_url"],
                            "name": file_info["name"],
                            "mimetype": file_info["mimetype"],
                            "created_time": block_created_time,
                            "last_edited_time": block_last_edited_time
                        })

                # Process other block types normally
                else:
                    self.logger.info(f"processing block {block_id} of type {block_type}")
                    processed_block = await self._process_block(block_id, block_type, block_content,
                                                block_created_time, block_last_edited_time)
                    if processed_block:
                        self.logger.info(f"processed block {block_id} of type {block_type}")
                        processed_blocks.append(processed_block)

            self.logger.info(f"Processed {len(processed_blocks)} blocks for page {page_id}")
            return processed_blocks

        except Exception as e:
            self.logger.error(f"Error fetching block children for page {page_id}: {str(e)}")
            raise

    async def _get_file_info(self, block_content, block_type, block_id):
        """
        Extract file information and return record ID with file details.
        This assumes records already exist and we're just retrieving information.
        """
        try:
            # Generate the expected record key (same logic as before)
            record_key = f"notion_file_{block_id}"

            # Extract file information based on block type
            if block_type == "file":
                file_type = block_content.get("type")
                if file_type == "external":
                    file_url = block_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_url = block_content.get("file", {}).get("url", "")
                else:
                    file_url = ""

                name = block_content.get("name", "Untitled")
                mimetype = self._extract_mimetype(block_content, block_type, name)

            elif block_type in ["video", "image", "gif"]:
                # For media blocks, the structure is different - content is nested
                file_type = block_content.get("type")

                file_url = ""
                if file_type == "external":
                    file_url = block_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_url = block_content.get("file", {}).get("url", "")
                elif file_type == "youtube" and block_type == "video":
                    file_url = block_content.get("youtube", {}).get("url", "")
                elif file_type == "vimeo" and block_type == "video":
                    file_url = block_content.get("vimeo", {}).get("url", "")

                # Extract name from media content or caption
                name = f"Untitled {block_type.title()}"
                if block_content.get("name"):
                    name = block_content.get("name")
                else:
                    caption = block_content.get("caption", [])
                    if caption and len(caption) > 0:
                        caption_text = caption[0].get("plain_text", "")
                        if caption_text:
                            name = caption_text

                mimetype = self._extract_mimetype(block_content, block_type, name)

            elif block_type == "audio":
                # For audio blocks, the structure is similar to other media
                audio_type = block_content.get("type")

                file_url = ""
                if audio_type == "external":
                    file_url = block_content.get("external", {}).get("url", "")
                elif audio_type == "file":
                    file_url = block_content.get("file", {}).get("url", "")

                # Extract name from audio content or caption
                name = "Untitled Audio"
                if block_content.get("name"):
                    name = block_content.get("name")
                else:
                    caption = block_content.get("caption", [])
                    if caption and len(caption) > 0:
                        caption_text = caption[0].get("plain_text", "")
                        if caption_text:
                            name = caption_text

                mimetype = self._extract_mimetype(block_content, block_type, name)

            else:
                return None

            # Return the file information with assumed existing record ID
            return {
                "record_id": record_key,
                "file_url": file_url,
                "name": name,
                "mimetype": mimetype
            }

        except Exception as e:
            self.logger.error(f"Error extracting file info for block {block_id}: {str(e)}")
            return None

    def _extract_mimetype(self, content, block_type, name):
        """Extract or infer mimetype from content and block type"""
        # Try to get mimetype from file content first
        mimetype = None
        if content.get("file"):
            mimetype = content.get("file", {}).get("mimetype") or content.get("file", {}).get("mime_type")

        # If no mimetype found, infer from block type and file extension
        if not mimetype:
            if block_type == "image":
                if name.lower().endswith(('.jpg', '.jpeg')):
                    mimetype = "image/jpeg"
                elif name.lower().endswith('.png'):
                    mimetype = "image/png"
                elif name.lower().endswith('.gif'):
                    mimetype = "image/gif"
                elif name.lower().endswith('.webp'):
                    mimetype = "image/webp"
                else:
                    mimetype = "image/unknown"
            elif block_type == "video":
                if name.lower().endswith(('.mp4', '.m4v')):
                    mimetype = "video/mp4"
                elif name.lower().endswith('.mov'):
                    mimetype = "video/quicktime"
                elif name.lower().endswith('.avi'):
                    mimetype = "video/x-msvideo"
                else:
                    mimetype = "video/unknown"
            elif block_type == "gif":
                mimetype = "image/gif"
            elif block_type == "audio":
                if name.lower().endswith(('.mp3', '.mpeg')):
                    mimetype = "audio/mpeg"
                elif name.lower().endswith('.wav'):
                    mimetype = "audio/wav"
                elif name.lower().endswith('.ogg'):
                    mimetype = "audio/ogg"
                elif name.lower().endswith('.m4a'):
                    mimetype = "audio/mp4"
                else:
                    mimetype = "audio/unknown"
            else:
                # For file type, try to infer from extension
                if name.lower().endswith(('.pdf',)):
                    mimetype = "application/pdf"
                elif name.lower().endswith(('.doc', '.docx')):
                    mimetype = "application/msword"
                elif name.lower().endswith(('.xls', '.xlsx')):
                    mimetype = "application/vnd.ms-excel"
                elif name.lower().endswith(('.ppt', '.pptx')):
                    mimetype = "application/vnd.ms-powerpoint"
                elif name.lower().endswith('.txt'):
                    mimetype = "text/plain"
                else:
                    mimetype = "application/octet-stream"

        return mimetype

    async def _process_block(self, block_id, block_type, block_content,
                        created_time, last_edited_time):
        """
        Process an individual Notion block and return formatted content.
        (Keep the existing implementation for non-file blocks)
        """
        try:
            # Convert timestamps to milliseconds
            creation_date = self._iso_to_timestamp(created_time) if created_time else None
            update_date = self._iso_to_timestamp(last_edited_time) if last_edited_time else None

            # Initialize the standard block structure
            formatted_block = {
                "block_id": block_id,
                "block_type": block_type,
                "block_name": "",
                "block_format": "txt",  # Default format
                "block_extension": [],
                "block_comments": [],
                "block_creation_date": creation_date,
                "block_update_date": update_date,
                "links": [],
                "data": None,
                "public_data_link": "",
                "public_data_link_expiration_epoch_time_in_ms": None,
                "weburl": f"https://www.notion.so/{block_id.replace('-', '')}"
            }

            # Process text-based blocks
            if block_type in ["paragraph", "heading_1", "heading_2", "heading_3",
                            "bulleted_list_item", "numbered_list_item", "quote", "callout"]:
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = f"{block_type}_{block_id[-8:]}"

                # Extract links from rich text if any
                links = self._extract_links_from_rich_text(block_content.get("rich_text", []))
                if links:
                    formatted_block["links"] = links

            # Process to-do items
            elif block_type == "to_do":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                checked = block_content.get("checked", False)
                formatted_block["data"] = {
                    "text": text_content,
                    "checked": checked
                }
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = f"todo_{block_id[-8:]}"

            # Process toggle blocks
            elif block_type == "toggle":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = f"toggle_{block_id[-8:]}"

            # Process code blocks
            elif block_type == "code":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                language = block_content.get("language", "")
                formatted_block["data"] = {
                    "code": text_content,
                    "language": language
                }
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"code_{language}" if language else f"code_{block_id[-8:]}"

            # Process divider
            elif block_type == "divider":
                formatted_block["data"] = "---"
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"divider_{block_id[-8:]}"

            # Process bookmark blocks
            elif block_type == "bookmark":
                url = block_content.get("url", "")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))

                formatted_block["data"] = {
                    "url": url,
                    "caption": caption
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url] if url else []
                formatted_block["block_name"] = f"bookmark_{block_id[-8:]}"

            # Process embed blocks
            elif block_type == "embed":
                url = block_content.get("url", "")
                caption = self._extract_text_from_rich_text(block_content.get("caption", []))

                formatted_block["data"] = {
                    "url": url,
                    "caption": caption
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url] if url else []
                formatted_block["block_name"] = f"embed_{block_id[-8:]}"

            # Process web_bookmark blocks
            elif block_type == "link_preview":
                url = block_content.get("url", "")

                formatted_block["data"] = {
                    "url": url
                }
                formatted_block["block_format"] = "txt"
                formatted_block["links"] = [url] if url else []
                formatted_block["block_name"] = f"link_preview_{block_id[-8:]}"

            # Process table blocks
            elif block_type == "table":
                # Table properties
                has_column_header = block_content.get("has_column_header", False)
                has_row_header = block_content.get("has_row_header", False)
                table_width = block_content.get("table_width", 0)

                table_data = {
                    "has_column_header": has_column_header,
                    "has_row_header": has_row_header,
                    "table_width": table_width,
                    "rows": []  # Would be populated by fetching child blocks
                }

                formatted_block["data"] = table_data
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"table_{table_width}x_columns"

            # Process equation blocks
            elif block_type == "equation":
                expression = block_content.get("expression", "")

                formatted_block["data"] = expression
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"equation_{block_id[-8:]}"

            # Process table of contents
            elif block_type == "table_of_contents":
                formatted_block["data"] = "Table of Contents"
                formatted_block["block_format"] = "markdown"
                formatted_block["block_name"] = f"toc_{block_id[-8:]}"

            # Process breadcrumb
            elif block_type == "breadcrumb":
                formatted_block["data"] = "Breadcrumb"
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = f"breadcrumb_{block_id[-8:]}"

            # Process synced blocks
            elif block_type == "synced_block":
                synced_from = block_content.get("synced_from")
                if synced_from:
                    original_block_id = synced_from.get("block_id")
                    formatted_block["data"] = {
                        "synced_from_id": original_block_id
                    }
                else:
                    formatted_block["data"] = {
                        "is_original": True
                    }
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = f"synced_{block_id[-8:]}"

            # Process template blocks
            elif block_type == "template":
                text_content = self._extract_text_from_rich_text(block_content.get("rich_text", []))
                formatted_block["data"] = text_content
                formatted_block["block_format"] = "txt"
                formatted_block["block_name"] = f"template_{block_id[-8:]}"

            else:
                # For any unsupported block types, store basic info
                formatted_block["data"] = f"Unsupported block type: {block_type}"
                formatted_block["block_name"] = f"unsupported_{block_type}_{block_id[-8:]}"

            # Fetch comments for this block
            try:
                comments = await self._fetch_comments(block_id)
                processed_comments = []
                for comment in comments:
                    comment_id = comment.get("id", "")
                    created_time = comment.get("created_time", "")
                    created_by = comment.get("created_by", {})
                    rich_text = comment.get("rich_text", [])

                    # Extract comment text
                    comment_text = self._extract_text_from_rich_text(rich_text)

                    if comment_text:
                        processed_comments.append({
                            "text": comment_text,
                            "format": "txt",
                            "threadId": comment_id,
                            "created_time": created_time,
                            "created_by": created_by.get("name", "Unknown")
                        })

                formatted_block["block_comments"] = processed_comments
            except Exception as comment_error:
                self.logger.warning(f"Could not fetch comments for block {block_id}: {str(comment_error)}")
                formatted_block["block_comments"] = []

            self.logger.info(f"formatted_block: {formatted_block}")

            return formatted_block

        except Exception as e:
            self.logger.error(f"Error processing block {block_id} of type {block_type}: {str(e)}")
            return None

    def _extract_text_from_rich_text(self, rich_text_array):
        """
        Helper function to extract plain text from Notion rich text array
        Args:
            rich_text_array (list): Notion rich text array
        Returns:
            str: Plain text content
        """
        if not rich_text_array or not isinstance(rich_text_array, list):
            return ""

        text_content = ""
        for item in rich_text_array:
            if isinstance(item, dict):
                text_content += item.get("plain_text", "")

        return text_content.strip()

    def _extract_links_from_rich_text(self, rich_text_array):
        """
        Extract links from Notion's rich text array
        Args:
            rich_text_array (list): Notion rich text array
        Returns:
            list: List of URLs found in the rich text
        """
        if not rich_text_array or not isinstance(rich_text_array, list):
            return []

        links = []
        for text_item in rich_text_array:
            if isinstance(text_item, dict) and text_item.get("href"):
                links.append(text_item.get("href"))

        return list(set(links))  # Remove duplicates


    async def _fetch_block_children(self, block_id):
        """Fetch all child blocks for a given block ID (page or block)."""
        try:
            self.logger.info(f"Fetching child blocks for block: {block_id}")
            url = f"{self.BASE_URL}/blocks/{block_id}/children"

            all_blocks = []
            has_more = True
            next_cursor = None

            async with aiohttp.ClientSession() as session:
                while has_more:
                    params = {"page_size": 100}
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    async with session.get(
                        url, headers=self.headers, params=params
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.error(
                                f"Failed to fetch block children: {error_text}"
                            )
                            raise Exception(f"Notion API error: {response.status}")

                        data = await response.json()
                        all_blocks.extend(data.get("results", []))

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

                self.logger.info(f"Successfully fetched {len(all_blocks)} child blocks")
                return all_blocks

        except Exception as e:
            self.logger.error(f"Error fetching block children: {str(e)}")
            raise

    def _iso_to_timestamp(self, iso_string: str) -> int:
        """Convert ISO date string to millisecond timestamp."""
        if not iso_string:
            return None
        try:
            dt = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return None

    async def _fetch_comments(self, block_id):
        """
        Fetch comments for the specific page or block
        Args:
            block_id (str): The block ID to fetch comments for
        Returns:
            list: List of comment dictionaries
        """
        try:
            self.logger.debug(f"Fetching comments for block: {block_id}")
            url = f"{self.BASE_URL}/comments"

            # API params - filter by the block_id to get comments for this specific block
            params = {
                "block_id": block_id,
                "page_size": 100
            }

            all_comments = []
            has_more = True
            next_cursor = None

            async with aiohttp.ClientSession() as session:
                while has_more:
                    if next_cursor:
                        params["start_cursor"] = next_cursor

                    async with session.get(url, headers=self.headers, params=params) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.logger.warning(f"Failed to fetch comments for block {block_id}: {error_text}")
                            # Return empty list instead of raising exception
                            return []

                        data = await response.json()
                        comments = data.get("results", [])
                        all_comments.extend(comments)

                        has_more = data.get("has_more", False)
                        next_cursor = data.get("next_cursor")

            self.logger.debug(f"Successfully fetched {len(all_comments)} comments for block {block_id}")
            return all_comments

        except Exception as e:
            self.logger.warning(f"Error fetching comments for block {block_id}: {str(e)}")
            # Return empty list instead of raising exception to not break the main flow
            return []

    async def fetch_file_data(self, block_id: str):
        """
        Fetch file data from Notion API and return structured file information with actual file content.
        Args:
            block_id (str): The block ID of the file block
        Returns:
            dict: Structured file data with all required fields including comments and file content
        """
        try:
            self.logger.info(f"Fetching file data for block: {block_id}")

            # Fetch the block data from Notion API
            url = f"{self.BASE_URL}/blocks/{block_id}"

            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=self.headers) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        self.logger.error(f"Failed to fetch block data: {error_text}")
                        raise Exception(f"Notion API error: {response.status}")

                    block_data = await response.json()

            # Extract basic block information
            block_type = block_data.get("type")
            # Extract creation and update dates
            created_time = block_data.get("created_time")
            last_edited_time = block_data.get("last_edited_time")

            # Convert ISO dates to timestamps
            creation_date = self._iso_to_timestamp(created_time) if created_time else None
            update_date = self._iso_to_timestamp(last_edited_time) if last_edited_time else None

            # Initialize common variables
            file_url = ""
            file_name = ""
            caption = []
            expiry_time = None
            block_format = "bin"  # Default format
            file_extensions = []
            file_data = None
            file_size = None
            mimetype = None

            # Handle different file block types
            if block_type == "file":
                file_content = block_data.get("file", {})
                file_type = file_content.get("type")

                # Extract file URL based on type
                if file_type == "external":
                    file_url = file_content.get("external", {}).get("url", "")
                elif file_type == "file":
                    file_info = file_content.get("file", {})
                    file_url = file_info.get("url", "")
                    expiry_time = file_info.get("expiry_time")

                file_name = file_content.get("name", "Untitled File")
                caption = file_content.get("caption", [])

            elif block_type in ["image", "video", "audio", "pdf"]:
                # Handle media blocks
                media_content = block_data.get(block_type, {})
                media_type = media_content.get("type")

                if media_type == "external":
                    file_url = media_content.get("external", {}).get("url", "")
                elif media_type == "file":
                    file_info = media_content.get("file", {})
                    file_url = file_info.get("url", "")
                    expiry_time = file_info.get("expiry_time")

                # Extract name from caption or use default
                caption = media_content.get("caption", [])
                if caption and len(caption) > 0:
                    file_name = caption[0].get("plain_text", f"Untitled {block_type.title()}")
                else:
                    file_name = f"Untitled {block_type.title()}"

            elif block_type == "paragraph":
                # Handle paragraph blocks
                paragraph_content = block_data.get("paragraph", {})
                rich_text = paragraph_content.get("rich_text", [])

                # Extract text content
                text_content = self._extract_text_from_rich_text(rich_text)

                file_name = f"Paragraph - {text_content[:50]}..." if len(text_content) > 50 else f"Paragraph - {text_content}"
                block_format = "txt"
                # For text blocks, store the text content as file data
                file_data = text_content.encode('utf-8')
                file_size = len(file_data)
                mimetype = "text/plain"

            elif block_type == "table":
                # Handle table blocks
                table_content = block_data.get("table", {})
                table_width = table_content.get("table_width", 0)

                file_name = f"Table ({table_width} columns)"
                block_format = "txt"
                # For table blocks, we could fetch the table data if needed
                table_data = f"Table with {table_width} columns"
                file_data = table_data.encode('utf-8')
                file_size = len(file_data)
                mimetype = "text/plain"

            else:
                # Handle other block types as text
                file_name = f"{block_type.title()} Block"
                block_format = "txt"
                # Store block type info as file data
                block_info = f"{block_type.title()} Block"
                file_data = block_info.encode('utf-8')
                file_size = len(file_data)
                mimetype = "text/plain"

            # Download actual file content if there's a file URL
            if file_url:
                try:
                    self.logger.info(f"Downloading file content from: {file_url}")
                    file_data, file_size, mimetype = await self._download_file_content(file_url)
                    self.logger.info(f"Successfully downloaded file content, size: {file_size} bytes")
                except Exception as download_error:
                    self.logger.warning(f"Failed to download file content: {str(download_error)}")
                    # Keep the file_data as None if download fails

            # Determine file extension and format from URL or file name
            if file_url:
                parsed_url = urlparse(file_url)
                file_path = parsed_url.path
                _, ext = os.path.splitext(file_path)
                if ext:
                    file_extensions = [ext.lower()]
                    # Determine format based on extension
                    if ext.lower() in ['.txt', '.md', '.html', '.json', '.xml', '.csv']:
                        block_format = "txt"
                    elif ext.lower() in ['.md', '.markdown']:
                        block_format = "markdown"
                    else:
                        block_format = "bin"

            # If no extension from URL, try to get from file name
            if not file_extensions and file_name:
                _, ext = os.path.splitext(file_name)
                if ext:
                    file_extensions = [ext.lower()]

            # Process caption/comments from block content
            block_comments = []
            if caption and isinstance(caption, list):
                for comment_item in caption:
                    if isinstance(comment_item, dict):
                        comment_text = comment_item.get("plain_text", "")
                        if comment_text:
                            block_comments.append({
                                "text": comment_text,
                                "format": "txt",  # Default format for captions
                                "threadId": f"{block_id}_caption_{len(block_comments)}"
                            })

            # Fetch additional comments from Notion API
            try:
                notion_comments = await self._fetch_comments(block_id)
                for comment in notion_comments:
                    comment_id = comment.get("id", "")
                    created_time_comment = comment.get("created_time", "")
                    created_by = comment.get("created_by", {})
                    rich_text = comment.get("rich_text", [])

                    # Extract comment text
                    comment_text = self._extract_text_from_rich_text(rich_text)

                    if comment_text:
                        block_comments.append({
                            "text": comment_text,
                            "format": "txt",
                            "threadId": comment_id,
                            "created_time": created_time_comment,
                            "created_by": created_by.get("name", "Unknown")
                        })
            except Exception as comment_error:
                self.logger.warning(f"Could not fetch comments for block {block_id}: {str(comment_error)}")

            # Convert expiry time to epoch milliseconds if available
            public_data_link_expiration = None
            if expiry_time:
                public_data_link_expiration = self._iso_to_timestamp(expiry_time)

            # Get web URL for the block
            web_url = f"https://www.notion.so/{block_id.replace('-', '')}"

            # Encode file_data if it's binary
            processed_file_data = file_data
            if file_data and isinstance(file_data, bytes):
                # Determine if this should be base64 encoded based on format
                if block_format == "bin":
                    processed_file_data = base64.b64encode(file_data).decode('utf-8')
                else:
                    # For text formats, try to decode as UTF-8
                    try:
                        processed_file_data = file_data.decode('utf-8')
                    except UnicodeDecodeError:
                        # If UTF-8 decoding fails, fall back to base64
                        processed_file_data = base64.b64encode(file_data).decode('utf-8')

            # Convert expiry time to epoch milliseconds if available
            public_data_link_expiration = None
            if expiry_time:
                public_data_link_expiration = self._iso_to_timestamp(expiry_time)

            # Get web URL for the block
            web_url = f"https://www.notion.so/{block_id.replace('-', '')}"

            # Structure the response according to the required format
            structured_data = {
                "block_id": block_id,
                "block_type": block_type,
                "block_name": file_name,
                "block_format": block_format,
                "block_extension": file_extensions,
                "block_comments": block_comments,
                "block_creation_date": creation_date,
                "block_update_date": update_date,
                "links": [file_url] if file_url else [],
                "data": processed_file_data,
                "public_data_link": file_url if file_url else None,
                "public_data_link_expiration_epoch_time_in_ms": public_data_link_expiration,
                "weburl": web_url
            }

            self.logger.info(f"Successfully fetched file data for block: {block_id}")
            return structured_data

        except Exception as e:
            self.logger.error(f"Error fetching file data for block {block_id}: {str(e)}")
            raise Exception(f"Failed to fetch file data: {str(e)}")

    def _determine_block_format_from_extension(self, extension: str) -> str:
        """
        Helper function to determine block format from file extension
        Args:
            extension (str): File extension (e.g., '.txt', '.pdf')
        Returns:
            str: Block format ('txt', 'bin', 'markdown')
        """
        if not extension:
            return "bin"

        ext = extension.lower()

        if ext in ['.txt', '.html', '.json', '.xml', '.csv', '.log']:
            return "txt"
        elif ext in ['.md', '.markdown']:
            return "markdown"
        else:
            return "bin"

    async def _download_file_content(self, file_url: str):
        """
        Download file content from the given URL.
        Args:
            file_url (str): The URL to download the file from
        Returns:
            tuple: (file_data, file_size, mimetype)
        """
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as response:
                    if response.status != 200:
                        raise Exception(f"Failed to download file: HTTP {response.status}")

                    # Get content type from headers
                    content_type = response.headers.get('content-type', 'application/octet-stream')

                    # Read file content
                    file_data = await response.read()
                    file_size = len(file_data)
                    self.logger.info(f"file_data type: {type(file_data)}")
                    self.logger.info(f"file_size: {file_size}")
                    self.logger.info(f"content_type: {content_type}")

                    return file_data, file_size, content_type

        except Exception as e:
            self.logger.error(f"Error downloading file from {file_url}: {str(e)}")
            raise


    def _determine_encoding_from_mimetype(self, mimetype: str) -> str:
        """
        Determine appropriate encoding based on mimetype.
        Args:
            mimetype (str): The MIME type of the file
        Returns:
            str: 'utf-8' for text files, 'base64' for binary files
        """
        if not mimetype:
            return "base64"

        text_types = [
            'text/',
            'application/json',
            'application/xml',
            'application/javascript',
            'application/x-javascript'
        ]

        for text_type in text_types:
            if mimetype.startswith(text_type):
                return "utf-8"

        return "base64"


    def _get_mimetype_from_extension(self, extension: str) -> str:
        """
        Get MIME type based on file extension.
        Args:
            extension (str): File extension (e.g., '.pdf', '.txt')
        Returns:
            str: MIME type
        """
        if not extension:
            return "application/octet-stream"

        ext = extension.lower()

        mime_types = {
            '.pdf': 'application/pdf',
            '.txt': 'text/plain',
            '.md': 'text/markdown',
            '.html': 'text/html',
            '.json': 'application/json',
            '.xml': 'application/xml',
            '.csv': 'text/csv',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.mp4': 'video/mp4',
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.doc': 'application/msword',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.xls': 'application/vnd.ms-excel',
            '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            '.ppt': 'application/vnd.ms-powerpoint',
            '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'
        }

        return mime_types.get(ext, 'application/octet-stream')
