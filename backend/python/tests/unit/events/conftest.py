"""Event-handler tests feed placeholder bytes as a PDF. The page count runs only in a parse worker process, which these
tests do not start, so it is stubbed to a small count; a test that exercises the count patches it itself."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _small_page_count() -> Iterator[None]:
    with patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new_callable=AsyncMock, return_value=1):
        yield
