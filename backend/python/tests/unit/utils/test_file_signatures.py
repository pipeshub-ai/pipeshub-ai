"""Unit tests for app.utils.file_signatures."""

import pytest

from app.utils.file_signatures import (
    METADATA_FILE_SIGNATURES,
    match_metadata_file_signature,
)

# The OLE2/CFBF container header shared by legacy .doc/.xls/.ppt (and other
# real formats). Regression guard: this must never be added as a metadata
# signature, since it would misclassify real documents as junk — see the
# module docstring in app.utils.file_signatures.
_OLE2_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
_PDF_MAGIC = b"%PDF-1.7\n"
_ZIP_MAGIC = b"PK\x03\x04"  # DOCX/XLSX/PPTX (OOXML) container header


class TestMetadataFileSignatures:
    """Structural tests for the signature table itself."""

    def test_is_dict_of_bytes(self):
        assert isinstance(METADATA_FILE_SIGNATURES, dict)
        for name, magic in METADATA_FILE_SIGNATURES.items():
            assert isinstance(name, str)
            assert isinstance(magic, bytes)
            assert len(magic) > 0

    def test_apple_double_magic(self):
        assert METADATA_FILE_SIGNATURES["AppleDouble"] == b"\x00\x05\x16\x07"

    def test_apple_single_magic(self):
        assert METADATA_FILE_SIGNATURES["AppleSingle"] == b"\x00\x05\x16\x00"

    def test_ds_store_magic_is_bud1_not_bab1(self):
        # Regression: the real .DS_Store header is "...Bud1", a prior version
        # of this table had a "BAB1" typo that could never match a real file.
        assert METADATA_FILE_SIGNATURES["DS_Store"] == b"\x00\x00\x00\x01Bud1"

    def test_no_signature_collides_with_ole2_container(self):
        # OLE2/CFBF is shared by legacy Office formats; a signature here
        # that matches it (or is a prefix of it) would silently drop real
        # .doc/.xls/.ppt documents as unsupported.
        for magic in METADATA_FILE_SIGNATURES.values():
            assert not _OLE2_MAGIC.startswith(magic)

    def test_no_signature_collides_with_pdf_or_ooxml(self):
        for magic in METADATA_FILE_SIGNATURES.values():
            assert not _PDF_MAGIC.startswith(magic)
            assert not _ZIP_MAGIC.startswith(magic)


class TestMatchMetadataFileSignature:
    @pytest.mark.parametrize(
        ("content", "expected_name"),
        [
            (b"\x00\x05\x16\x07", "AppleDouble"),
            (b"\x00\x05\x16\x07\x00\x02\x00\x00" + b"\x00" * 16, "AppleDouble"),
            (b"\x00\x05\x16\x00", "AppleSingle"),
            (b"\x00\x00\x00\x01Bud1" + b"\x00" * 32, "DS_Store"),
            (b"bplist00" + b"\x00" * 10, "BinaryPlist"),
            (b"\x4c\x00\x00\x00\x01\x14\x02\x00" + b"\x00" * 8, "WindowsLnk"),
            (b"b0VIM" + b" 8.2\n" + b"\x00" * 10, "VimSwap"),
        ],
    )
    def test_known_signatures_are_matched(self, content, expected_name):
        assert match_metadata_file_signature(content) == expected_name

    def test_full_multibyte_magic_is_checked_not_just_first_4_bytes(self):
        # match_metadata_file_signature uses startswith(magic), which checks
        # the *entire* magic value regardless of its length — there is no
        # hardcoded 4-byte prefix comparison anywhere in this module. Prove
        # it for BinaryPlist's 6-byte magic: content sharing only the first
        # 4 bytes ("bpli") must NOT match, only content sharing all 6 does.
        assert len(METADATA_FILE_SIGNATURES["BinaryPlist"]) == 6
        assert match_metadata_file_signature(b"bpliXX" + b"\x00" * 10) is None
        assert match_metadata_file_signature(b"bplist" + b"\x00" * 10) == "BinaryPlist"

    def test_real_pdf_is_not_matched(self):
        assert match_metadata_file_signature(_PDF_MAGIC + b"rest of pdf content") is None

    def test_real_ooxml_docx_is_not_matched(self):
        assert match_metadata_file_signature(_ZIP_MAGIC + b"rest of docx content") is None

    def test_real_ole2_doc_is_not_matched(self):
        # The exact scenario this table must never regress into matching.
        assert match_metadata_file_signature(_OLE2_MAGIC + b"rest of doc content") is None

    def test_plain_text_is_not_matched(self):
        assert match_metadata_file_signature(b"Hello, world!") is None

    def test_empty_bytes_returns_none(self):
        assert match_metadata_file_signature(b"") is None

    def test_content_shorter_than_magic_returns_none(self):
        # Truncated/partial AppleDouble magic must not false-match.
        assert match_metadata_file_signature(b"\x00\x05\x16") is None

    def test_bytearray_input_is_supported(self):
        assert match_metadata_file_signature(bytearray(b"\x00\x05\x16\x07")) == "AppleDouble"

    @pytest.mark.parametrize("non_bytes", [None, "not bytes", 12345, {}, [], object()])
    def test_non_bytes_input_returns_none(self, non_bytes):
        assert match_metadata_file_signature(non_bytes) is None
