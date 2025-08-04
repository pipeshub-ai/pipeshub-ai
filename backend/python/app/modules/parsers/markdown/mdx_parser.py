import re


class MDXParser:
    """Parser for MDX files to convert them to regular Markdown"""

    def __init__(self) -> None:
        # Tags to preserve with special handling
        self.allowed_tags = ['CodeGroup', 'Info', 'AccordionGroup', 'Accordion']

        # Regex for full JSX blocks (e.g., <Tag>...</Tag>)
        self.jsx_block_pattern = r'<(?P<tag>[A-Za-z][A-Za-z0-9]*)[^>]*>(?P<content>.*?)</\1>'
        self.self_closing_pattern = r'<[A-Za-z][A-Za-z0-9]*[^>]*/>'
        self.orphan_closing_tag_pattern = r'</[A-Za-z][A-Za-z0-9]*>'
        self.extra_newlines_pattern = r'\n\s*\n'

        # Special handling for title attribute in Accordion
        self.accordion_tag_with_title_pattern = r'<Accordion\s+title=[\'"](.+?)[\'"]\s*>(.*?)</Accordion>'

    def convert_mdx_to_md(self, binary_content: bytes) -> bytes:
        try:
            content = binary_content.decode('utf-8')

            # Step 1: Handle <Accordion title="...">...</Accordion> first
            content = re.sub(
                self.accordion_tag_with_title_pattern,
                lambda m: f"\n\n### {m.group(1)}\n\n{m.group(2).strip()}\n",
                content,
                flags=re.DOTALL
            )

            # Step 2: Handle allowed block JSX tags
            def handle_allowed_tags(match) -> str:
                tag = match.group("tag")
                inner = match.group("content").strip()
                if tag == "Info":
                    return f"\n\n> **Note:**\n>\n> {inner}\n"
                elif tag == "CodeGroup":
                    return f"\n\n{inner}\n"
                elif tag == "AccordionGroup":
                    return f"\n\n{inner}\n"
                # Default passthrough
                return inner

            content = re.sub(
                self.jsx_block_pattern,
                lambda m: handle_allowed_tags(m) if m.group("tag") in self.allowed_tags else '',
                content,
                flags=re.DOTALL
            )

            # Step 3: Remove all remaining self-closing or unknown JSX tags
            content = re.sub(self.self_closing_pattern, '', content, flags=re.DOTALL)
            content = re.sub(self.orphan_closing_tag_pattern, '', content)

            # Step 4: Clean up excess whitespace
            content = re.sub(self.extra_newlines_pattern, '\n\n', content)

            return content.strip().encode('utf-8')

        except Exception as e:
            raise Exception(f"Error converting MDX to Markdown: {str(e)}") from e
