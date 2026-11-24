"""A unit test anywhere in the tree parses PDFs without downloading the table model (tests/unit/conftest.py).

A transformers test parsed a real PDF outside the parser tests, where the switch used to be set. Its worker thread
waited on a stalled Hub download past pytest-timeout, and the pytest worker could not exit until the job timed out.
"""

import numpy as np
import pytest

from app.modules.parsers.pdf import table_detector


def test_a_pdf_page_outside_the_parser_tests_never_reaches_the_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    downloads: list[str] = []

    def hf_hub_download(repo_id: str, *_args: object, **_kwargs: object) -> str:
        downloads.append(repo_id)
        raise OSError("no network in unit tests")

    monkeypatch.setattr("huggingface_hub.hf_hub_download", hf_hub_download)
    monkeypatch.setattr(table_detector, "_sessions", {})

    assert table_detector.detect_layout_boxes(np.zeros((32, 32, 3), np.uint8)) is None
    assert downloads == []
