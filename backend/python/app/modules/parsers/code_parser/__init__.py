"""Tree-sitter based parsing of source files into code blocks."""
from app.modules.parsers.code_parser.code_file_parser import CodeFileParser
from app.modules.parsers.code_parser.file_role import (
    FileRole,
    classify_file_role,
    is_ignored_path,
    should_index_code_file,
)
from app.modules.parsers.code_parser.lang_config import (
    SUPPORTED_CODE_EXTENSIONS,
    detect_language,
)

__all__ = [
    "SUPPORTED_CODE_EXTENSIONS",
    "CodeFileParser",
    "FileRole",
    "classify_file_role",
    "detect_language",
    "is_ignored_path",
    "should_index_code_file",
]
