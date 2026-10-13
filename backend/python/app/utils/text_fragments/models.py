from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceFormat(str, Enum):
    """How a snippet's text was authored, which decides how it is rendered on the page."""

    MARKDOWN = "markdown"
    HTML = "html"
    PLAIN = "plain"

    @classmethod
    def from_data_format(cls, value: object) -> SourceFormat | None:
        """Map a block `DataFormat` (enum or its string value) to a `SourceFormat`.

        Returns None for unknown or missing formats so the generator applies its
        configured default.
        """
        raw = getattr(value, "value", value)
        if not isinstance(raw, str):
            return None
        return _DATA_FORMAT_TO_SOURCE_FORMAT.get(raw.lower())


_DATA_FORMAT_TO_SOURCE_FORMAT: dict[str, SourceFormat] = {
    "markdown": SourceFormat.MARKDOWN,
    "html": SourceFormat.HTML,
    "txt": SourceFormat.PLAIN,
    "utf8": SourceFormat.PLAIN,
    "csv": SourceFormat.PLAIN,
    "json": SourceFormat.PLAIN,
    "xml": SourceFormat.PLAIN,
    "yaml": SourceFormat.PLAIN,
    "patch": SourceFormat.PLAIN,
    "diff": SourceFormat.PLAIN,
    "code": SourceFormat.PLAIN,
}


@dataclass(frozen=True)
class TextDirective:
    """A parsed `text=` directive. Terms are plain (decoded) text, never empty strings."""

    start: str
    end: str | None = None
    prefix: str | None = None
    suffix: str | None = None

    def __post_init__(self) -> None:
        if not self.start:
            raise ValueError("TextDirective.start must be non-empty")
        for name in ("end", "prefix", "suffix"):
            if getattr(self, name) == "":
                raise ValueError(f"TextDirective.{name} must be None or non-empty")


@dataclass(frozen=True)
class TextFragmentConfig:
    """Tunables for directive generation.

    Exact match is capped well below the spec's 300 characters: PipesHub builds
    snippets from parsed block text rather than the live DOM, so a long exact
    string breaks on any rendering difference, and web-search URLs are copied by
    an LLM where length costs tokens.
    """

    exact_max_words: int = 8
    exact_max_chars: int = 300
    range_term_words: int = 4
    min_term_alnum_chars: int = 3
    cjk_term_chars: int = 10
    default_format: SourceFormat = SourceFormat.MARKDOWN

    def __post_init__(self) -> None:
        if self.exact_max_words < 1 or self.range_term_words < 1:
            raise ValueError("word limits must be >= 1")
        if self.min_term_alnum_chars < 1 or self.cjk_term_chars < 1:
            raise ValueError("character limits must be >= 1")
