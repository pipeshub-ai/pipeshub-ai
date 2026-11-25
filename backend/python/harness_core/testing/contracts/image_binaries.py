"""Contract suite for ``ImageBinaryReader`` adapters (EH2, IBR-1..IBR-3).

Subclass ``ImageBinaryReaderContract`` with a ``subject`` fixture: a reader and the id of an image that holds
``CONTRACT_FILES``, readable by the sandbox user.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.testing.engines.binary import Directory, Link, sha256_digest

if TYPE_CHECKING:
    from harness_core.engines.binary import ImageBinaryReader
    from harness_core.testing.engines.binary import Entry

TOOL = "/opt/pxh-ibr/bin/tool"
LINK = "/opt/pxh-ibr/bin/link"
FOLDER = "/opt/pxh-ibr/folder"
MISSING = "/opt/pxh-ibr/bin/missing"
TOOL_BYTES = b"#!/bin/sh\necho pxh-ibr tool\n"
CONTRACT_FILES: dict[str, Entry] = {TOOL: TOOL_BYTES, LINK: Link(TOOL), FOLDER: Directory()}

Subject = tuple["ImageBinaryReader", str]


class ImageBinaryReaderContract:
    async def test_ibr1_a_regular_file_is_its_own_sha256(self, subject: Subject) -> None:
        reader, image_id = subject
        seen = await reader.observe(image_id, TOOL)
        assert (seen.path, seen.resolved, seen.digest) == (TOOL, TOOL, sha256_digest(TOOL_BYTES))

    async def test_ibr2_a_link_resolves_to_its_target(self, subject: Subject) -> None:
        reader, image_id = subject
        seen = await reader.observe(image_id, LINK)
        assert (seen.path, seen.resolved, seen.digest) == (LINK, TOOL, sha256_digest(TOOL_BYTES))

    async def test_ibr3_no_regular_file_has_no_digest(self, subject: Subject) -> None:
        reader, image_id = subject
        assert (await reader.observe(image_id, MISSING)).digest is None
        assert (await reader.observe(image_id, FOLDER)).digest is None


__all__ = ["CONTRACT_FILES", "FOLDER", "LINK", "MISSING", "TOOL", "TOOL_BYTES", "ImageBinaryReaderContract", "Subject"]
