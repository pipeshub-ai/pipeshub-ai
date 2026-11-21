"""The identity names the parser that runs: the in-process dispatch or, on the service pipeline, the service's own.

``file_types.legacy_route`` and ``file_types.parse_format`` name different parsers for some labels (``application/json`` with a
``csv`` extension is a CSV in process and JSON in the service). A key derived by the first for a copy the second built would
let a same-bytes update reuse the copy for a record the service reads differently.
"""
from __future__ import annotations

import itertools

import pytest

from app.config import file_types
from app.modules.pipeline import parser_identity as pi

MIMES = [m for m in sorted(file_types.MIME_FORMATS) if m] + ["text/plain", "application/octet-stream", None]
EXTENSIONS = [e for e in sorted(file_types._BY_EXTENSION) if e] + [None]


def _name(ext: str | None) -> str:
    return "f" + (f".{ext}" if ext else "")


def test_a_service_key_is_the_service_format_for_every_label_pair() -> None:
    checked = 0
    for mime, ext in itertools.product(MIMES, EXTENSIONS):
        key = pi.parser_key(mime, ext, _name(ext), "FILE", service=True)
        if key is None:
            continue
        assert key.split("@")[0] == file_types.parse_format(mime, ext), (mime, ext)
        checked += 1
    assert checked > 1000


def test_a_json_with_a_csv_extension_is_json_on_the_service_and_csv_in_process() -> None:
    assert pi.parser_key("application/json", "csv", "a.csv", "FILE", service=True) == "json@1"
    assert pi.parser_key("application/json", "csv", "a.csv", "FILE", service=False) == "csv@1"
    assert pi.parser_identity("application/json", "csv", "a.csv", "FILE", pipeline=pi.PIPELINE_SERVICE) == "svc:json@1"


def test_a_csv_is_not_taken_for_the_json_copy_the_service_built() -> None:
    built = pi.parser_key("application/json", "csv", "a.csv", "FILE", service=True)
    assert built != pi.parser_key("text/csv", "csv", "b.csv", "FILE", service=True)


@pytest.mark.parametrize("flag,expected", [("true", True), ("false", False), ("", False)])
def test_the_store_side_follows_the_process_setting(monkeypatch: pytest.MonkeyPatch, flag: str, expected: bool) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", flag)
    assert pi.service_pipeline_configured() is expected
    assert (pi.parser_key("application/json", "csv", "a.csv", "FILE") == "json@1") is expected
