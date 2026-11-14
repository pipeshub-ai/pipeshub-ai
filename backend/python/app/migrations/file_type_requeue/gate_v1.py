"""The indexing gate's decision before the file-type registry learnt any new type: frozen, never edited.

The re-queue migration selects a record only where this gate refused it and today's gate accepts it,
so it must keep answering as the gate did then for every input, not only the ones a snapshot pinned.
It is the gate's own code (the extension derivation and the decision of the record handler) over
copies of the lists that gate read. When the gate changes again, freeze that one beside this.
"""
from __future__ import annotations

from typing import Final

__all__ = ["supported"]

_GATE_MIMES: Final = (
    'application/blocks', 'application/dart',
    'application/epub+zip', 'application/javascript',
    'application/json', 'application/msword',
    'application/pdf', 'application/typescript',
    'application/vnd.google-apps.document', 'application/vnd.google-apps.presentation',
    'application/vnd.google-apps.spreadsheet', 'application/vnd.ms-excel',
    'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.sql.table', 'application/vnd.sql.view',
    'application/x-sh', 'application/x-yaml',
    'application/yaml', 'image/jpeg',
    'image/jpg', 'image/png',
    'image/svg+xml', 'image/webp',
    'text/csv', 'text/gmail_content',
    'text/html', 'text/javascript',
    'text/markdown', 'text/mdx',
    'text/plain', 'text/tab-separated-values',
    'text/x-c', 'text/x-c++',
    'text/x-csharp', 'text/x-go',
    'text/x-java-source', 'text/x-kotlin',
    'text/x-php', 'text/x-python',
    'text/x-python-script', 'text/x-ruby',
    'text/x-rust', 'text/x-script.python',
    'text/x-sh', 'text/x-shellscript',
    'text/x-swift',
)
_GATE_EXTENSIONS: Final = (
    'bash', 'c', 'cc', 'cjs', 'cpp', 'cs', 'csv', 'cxx',
    'dart', 'doc', 'docx', 'epub', 'go', 'h', 'hpp', 'htm',
    'html', 'hxx', 'java', 'jpeg', 'jpg', 'js', 'json', 'jsx',
    'kt', 'kts', 'md', 'mdx', 'mjs', 'pdf', 'php', 'png',
    'ppt', 'pptx', 'py', 'rb', 'rs', 'sh', 'sql_table', 'sql_view',
    'svg', 'swift', 'ts', 'tsv', 'tsx', 'txt', 'webp', 'xls',
    'xlsx', 'yaml', 'yml',
)
_CODE_FILE_EXTENSIONS: Final = (
    'bash', 'c', 'cc', 'cjs', 'clj', 'cljs', 'cpp', 'cs',
    'css', 'cxx', 'dart', 'erl', 'ex', 'exs', 'fish', 'go',
    'gql', 'gradle', 'graphql', 'groovy', 'h', 'hpp', 'hs', 'htm',
    'html', 'hxx', 'java', 'js', 'jsx', 'kt', 'kts', 'less',
    'lua', 'md', 'mjs', 'php', 'pl', 'pm', 'proto', 'ps1',
    'py', 'pyi', 'r', 'rb', 'rs', 'sass', 'scala', 'scss',
    'sh', 'sql', 'svelte', 'swift', 'tf', 'tfvars', 'ts', 'tsx',
    'vue', 'zsh',
)
_MIME_EXTENSION_HINTS: Final[dict[str, str]] = {
    'application/acrobat': 'pdf',
    'application/csv': 'csv',
    'application/csv; charset=utf-8': 'csv',
    'application/doc': 'doc',
    'application/excel': 'xls',
    'application/html': 'html',
    'application/markdown': 'md',
    'application/mdx': 'mdx',
    'application/msword': 'doc',
    'application/msword; charset=utf-8': 'doc',
    'application/pdf': 'pdf',
    'application/png': 'png',
    'application/powerpoint': 'ppt',
    'application/svg+xml': 'svg',
    'application/text': 'txt',
    'application/tsv': 'tsv',
    'application/txt': 'txt',
    'application/vnd.ms-excel': 'xls',
    'application/vnd.ms-excel.sheet.macroEnabled.12': 'xls',
    'application/vnd.ms-excel; charset=utf-8': 'xls',
    'application/vnd.ms-powerpoint': 'ppt',
    'application/vnd.ms-powerpoint.presentation.macroEnabled.12': 'ppt',
    'application/vnd.ms-powerpoint; charset=utf-8': 'ppt',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'pptx',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation+xml': 'pptx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet+xml': 'xlsx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'docx',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document+xml': 'docx',
    'application/vnd.pdf': 'pdf',
    'application/x-doc': 'doc',
    'application/x-excel': 'xls',
    'application/x-markdown': 'md',
    'application/x-mdx': 'mdx',
    'application/x-msexcel': 'xls',
    'application/x-mspowerpoint': 'ppt',
    'application/x-msword': 'doc',
    'application/x-msword; charset=utf-8': 'doc',
    'application/x-pdf': 'pdf',
    'application/x-png': 'png',
    'application/x-ppt': 'ppt',
    'application/x-xls': 'xls',
    'application/xhtml': 'html',
    'application/xhtml+xml': 'html',
    'application/xls': 'xls',
    'application/xml-svg': 'svg',
    'image/jpeg': 'jpeg',
    'image/jpeg; charset=binary': 'jpeg',
    'image/jpg': 'jpg',
    'image/jpg; charset=binary': 'jpg',
    'image/pjpeg': 'jpeg',
    'image/png': 'png',
    'image/png; charset=binary': 'png',
    'image/svg': 'svg',
    'image/svg+xml': 'svg',
    'image/svg+xml; charset=utf-8': 'svg',
    'image/vnd.mozilla.apng': 'png',
    'image/webp': 'webp',
    'image/x-jpeg': 'jpeg',
    'image/x-jpg': 'jpg',
    'image/x-png': 'png',
    'image/x-webp': 'webp',
    'text/comma-separated-values': 'csv',
    'text/csv': 'csv',
    'text/csv; charset=us-ascii': 'csv',
    'text/csv; charset=utf-8': 'csv',
    'text/gmail_content': 'html',
    'text/html': 'html',
    'text/html; charset=ascii': 'html',
    'text/html; charset=iso-8859-1': 'html',
    'text/html; charset=us-ascii': 'html',
    'text/html; charset=utf-8': 'html',
    'text/html; charset=windows-1252': 'html',
    'text/markdown': 'md',
    'text/markdown; charset=us-ascii': 'md',
    'text/markdown; charset=utf-8': 'md',
    'text/mdx': 'mdx',
    'text/mdx; charset=utf-8': 'mdx',
    'text/pdf': 'pdf',
    'text/plain': 'txt',
    'text/plain; charset=ascii': 'txt',
    'text/plain; charset=iso-8859-1': 'txt',
    'text/plain; charset=us-ascii': 'txt',
    'text/plain; charset=utf-8': 'txt',
    'text/plain; charset=windows-1252': 'txt',
    'text/tab-separated-values': 'tsv',
    'text/tab-separated-values; charset=utf-8': 'tsv',
    'text/tsv': 'tsv',
    'text/tsv; charset=utf-8': 'tsv',
    'text/txt': 'txt',
    'text/x-comma-separated-values': 'csv',
    'text/x-csv': 'csv',
    'text/x-markdown': 'md',
    'text/x-md': 'md',
    'text/x-mdx': 'mdx',
    'text/x-pdf': 'pdf',
    'text/x-text': 'txt',
    'text/xhtml': 'html',
    'text/xml-svg': 'svg',
    'zz-application/zz-winassoc-doc': 'doc',
    'zz-application/zz-winassoc-ppt': 'ppt',
    'zz-application/zz-winassoc-xls': 'xls',
}


def supported(
    *,
    extension: object,
    mime: object,
    record_name: object,
    is_code_file: bool,
) -> bool | None:
    """Whether the gate let the record through, or None where it raised.

    ``extension`` and ``mime`` are what the record event carried; a missing or "unknown" mime is
    replaced by the stored one before the call, as the handler does. The lists are tuples, as the
    gate's were, so a malformed value compares as it did there instead of raising.
    """
    try:
        if mime == "unknown" or not mime:
            mime = "unknown"
        code_file_extension = None
        if is_code_file and record_name and "." in record_name:  # type: ignore[operator]
            code_file_extension = record_name.rsplit(".", 1)[-1].lower()  # type: ignore[attr-defined]
        if (extension is None or extension == "unknown") and mime is not None and mime != "unknown":
            derived = _MIME_EXTENSION_HINTS.get(mime)  # type: ignore[call-overload]
            if derived:
                extension = derived
        if extension == "unknown" and mime != "text/gmail_content" and record_name and "." in record_name:  # type: ignore[operator]
            extension = record_name.split(".")[-1]  # type: ignore[attr-defined]
        if is_code_file:
            return code_file_extension in _CODE_FILE_EXTENSIONS or code_file_extension in _GATE_EXTENSIONS
        return mime in _GATE_MIMES or extension in _GATE_EXTENSIONS
    except (TypeError, AttributeError):
        return None
