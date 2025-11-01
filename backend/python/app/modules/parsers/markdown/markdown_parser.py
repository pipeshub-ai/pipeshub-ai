
import re
from typing import Dict, List, Tuple

import markdown
from bs4 import BeautifulSoup
from docling.datamodel.document import DoclingDocument
from docling.document_converter import DocumentConverter


class MarkdownParser:
    def __init__(self) -> None:
        self.converter = DocumentConverter()

    def parse_string(self, md_content: str) -> bytes:
        """
        Parse Markdown content from a string.

        Args:
            md_content (str): Markdown content as a string

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        # Convert string to bytes

        html = markdown.markdown(md_content, extensions=["md_in_html"])
        md_bytes = html.encode("utf-8")
        return md_bytes

    def parse_file(self, file_path: str) -> DoclingDocument:
        """
        Parse Markdown content from a file.

        Args:
            file_path (str): Path to the Markdown file

        Returns:
            Document: Parsed Docling document

        Raises:
            ValueError: If parsing fails
        """
        result = self.converter.convert(file_path)

        if result.status.value != "success":
            raise ValueError(f"Failed to parse Markdown: {result.status}")

        return result.document

    def extract_and_replace_images(self, md_content: str) -> Tuple[str, List[Dict[str, str]]]:
        """
        Extract all images from markdown content (inline, reference-style, and HTML syntax)
        and replace their alt text sequentially.

        Args:
            md_content (str): The content of the markdown file

        Returns:
            Tuple[str, List[Dict[str, str]]]:
                - Modified markdown content with replaced alt text
                - List of dictionaries containing image details:
                    {
                        'original_text': str,
                        'url': str,
                        'alt_text': str,
                        'new_alt_text': str,
                        'image_type': str  # 'markdown', 'reference', or 'html'
                    }
        """
        images: List[Dict[str, str]] = []
        image_counter = [1]  # Use list to allow modification in nested function

        # Pattern for markdown inline images: ![alt text](url "optional title")
        # Captures URL only (non-whitespace chars), optional title is matched but not captured
        markdown_img_pattern = r'!\[([^\]]*)\]\(([^\s)]+)(?:\s+"[^"]*")?\)'

        # Pattern for reference-style image usage: ![alt text][reference]
        reference_usage_pattern = r'!\[([^\]]*)\]\[([^\]]+)\]'

        # Pattern for reference definitions: [reference]: url "optional title"
        # Captures URL only (non-whitespace chars), optional title is matched but not captured
        reference_def_pattern = r'^\[([^\]]+)\]:\s+([^\s]+)(?:\s+"[^"]*")?\s*$'

        # First, collect all reference definitions
        reference_map = {}
        for match in re.finditer(reference_def_pattern, md_content, re.MULTILINE):
            ref_id = match.group(1)
            ref_url = match.group(2).strip()
            reference_map[ref_id] = ref_url

        # Track reference-style image positions to avoid processing as inline
        reference_positions = set()
        for match in re.finditer(reference_usage_pattern, md_content):
            reference_positions.add(match.start())

        # Replacer function for reference-style images
        def replace_reference_image(match) -> str:
            original_alt = match.group(1)
            ref_id = match.group(2)
            original_text = match.group(0)

            # Get URL from reference map
            url = reference_map.get(ref_id, f"[unknown reference: {ref_id}]")

            new_alt = f"Image_{image_counter[0]}"

            # Store image info
            images.append({
                'original_text': original_text,
                'url': url,
                'alt_text': original_alt,
                'new_alt_text': new_alt,
                'image_type': 'reference'
            })

            image_counter[0] += 1
            return f"![{new_alt}][{ref_id}]"

        # Replacer function for markdown inline images
        def replace_markdown_image(match) -> str:
            # Skip if this position was already processed as reference-style
            if match.start() in reference_positions:
                return match.group(0)

            original_alt = match.group(1)
            url = match.group(2)
            original_text = match.group(0)
            new_alt = f"Image_{image_counter[0]}"

            # Store image info
            images.append({
                'original_text': original_text,
                'url': url,
                'alt_text': original_alt,
                'new_alt_text': new_alt,
                'image_type': 'markdown'
            })

            image_counter[0] += 1
            return f"![{new_alt}]({url})"

        # Process HTML images using BeautifulSoup
        def process_html_images(content: str) -> str:
            """
            Use BeautifulSoup to properly parse and replace HTML img tags.
            This is more robust than regex for handling various HTML formats.
            """
            # Parse with html.parser to handle fragments
            soup = BeautifulSoup(content, 'html.parser')

            # Find all img tags
            for img_tag in soup.find_all('img'):
                # Get src and alt attributes
                src = img_tag.get('src', '')
                original_alt = img_tag.get('alt', '')

                # Store original HTML
                original_text = str(img_tag)

                # Create new alt text
                new_alt = f"Image_{image_counter[0]}"

                # Update the alt attribute
                img_tag['alt'] = new_alt

                # Store image info
                images.append({
                    'original_text': original_text,
                    'url': src,
                    'alt_text': original_alt,
                    'new_alt_text': new_alt,
                    'image_type': 'html'
                })

                image_counter[0] += 1

            return str(soup)

        # Apply replacements in sequence: reference-style, then inline, then HTML
        modified_content = re.sub(reference_usage_pattern, replace_reference_image, md_content)
        modified_content = re.sub(markdown_img_pattern, replace_markdown_image, modified_content)
        modified_content = process_html_images(modified_content)

        return modified_content, images



def txt_to_markdown(
    input_content: str,
    detect_structure: bool = True,
    preserve_formatting: bool = True
) -> str:
    text = str(input_content)

    if not detect_structure:
        # Simple conversion: just return as-is in markdown
        markdown = text
    else:
        # Apply structure detection
        markdown = _detect_and_convert_structure(text, preserve_formatting)

    return markdown

def _detect_and_convert_structure(text: str, preserve_formatting: bool) -> str:
    """
    Detect common text patterns and convert them to Markdown syntax.
    """
    MAX_HEADER_WORDS = 10
    MIN_UNDERLINE_LENGTH = 3

    lines = text.split('\n')
    markdown_lines = []
    in_code_block = False

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Skip empty lines but preserve them if needed
        if not stripped:
            if preserve_formatting or (i > 0 and markdown_lines):
                markdown_lines.append('')
            continue

        # Detect code blocks (lines starting with 4+ spaces or tabs)
        if line.startswith('    ') or line.startswith('\t'):
            if not in_code_block:
                markdown_lines.append('```')
                in_code_block = True
            markdown_lines.append(line.lstrip())
            continue
        elif in_code_block:
            markdown_lines.append('```')
            in_code_block = False

        # Detect headers (ALL CAPS lines, or lines followed by === or ---)
        if stripped.isupper() and len(stripped.split()) <= MAX_HEADER_WORDS:
            markdown_lines.append(f"## {stripped}")
            continue

        # Check for underlined headers
        if i < len(lines) - 1:
            next_line = lines[i + 1].strip()
            if next_line and all(c in '=-' for c in next_line) and len(next_line) >= MIN_UNDERLINE_LENGTH:
                level = 1 if '=' in next_line else 2
                markdown_lines.append(f"{'#' * level} {stripped}")
                lines[i + 1] = ''  # Skip the underline
                continue

        # Detect numbered lists
        if re.match(r'^\d+[\.\)]\s+', stripped):
            markdown_lines.append(re.sub(r'^(\d+)[\.\)]\s+', r'\1. ', stripped))
            continue

        # Detect bullet points
        if re.match(r'^[-*•]\s+', stripped):
            markdown_lines.append(re.sub(r'^[-*•]\s+', '- ', stripped))
            continue

        # Detect emphasis (words in *asterisks* or _underscores_)
        line_converted = stripped

        line_converted = re.sub(r'\*([^*]+)\*', r'**\1**', line_converted)
        line_converted = re.sub(r'_([^_]+)_', r'*\1*', line_converted)

        # Detect URLs and convert to links
        line_converted = re.sub(
            r'(?<![\(\[])(https?://[^\s\)]+)',
            r'[\1](\1)',
            line_converted
        )

        markdown_lines.append(line_converted)

    # Close code block if still open
    if in_code_block:
        markdown_lines.append('```')

    return '\n'.join(markdown_lines)

