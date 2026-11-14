"""The libraries that read .msg files are permissively licensed, and no GPL mail reader is installed.

PipesHub is Apache-2.0: a GPL dependency (extract-msg, RTFDE) would change what the project can be
shipped under.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util

import pytest

_PERMISSIVE = {
    "python-oxmsg": "MIT",
    "compressed-rtf": "MIT",
    "striprtf": "BSD",
    "olefile": "BSD",
}


def _license(distribution: str) -> str:
    meta = importlib.metadata.metadata(distribution)
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    return " ".join([meta.get("License-Expression") or "", meta.get("License") or "", *classifiers])


@pytest.mark.parametrize(("distribution", "expected"), sorted(_PERMISSIVE.items()))
def test_each_mail_library_is_permissively_licensed(distribution: str, expected: str) -> None:
    text = _license(distribution)
    assert expected in text, text
    assert "GPL" not in text.replace("LGPL", "GPL"), text


@pytest.mark.parametrize("module", ["extract_msg", "RTFDE"])
def test_no_gpl_mail_reader_is_importable(module: str) -> None:
    assert importlib.util.find_spec(module) is None
