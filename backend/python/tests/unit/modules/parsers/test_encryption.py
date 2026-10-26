"""Finding out from the file itself that it needs a password."""
from __future__ import annotations

import struct
import time

import pypdfium2
import pytest

from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.parsers.encryption import (
    has_encrypted_package,
    pdfium_password_error,
    raise_if_password_protected_office,
    raise_if_pdf_needs_password,
)
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from tests.support.office_samples import (
    ENCRYPTED_BY_EXTENSION,
    ENCRYPTED_MACRO_BY_EXTENSION,
    MACRO_MIME,
    PLAIN_BY_EXTENSION,
    cfbf,
)
from tests.support.pdf_samples import (
    owner_password_pdf,
    password_protected_pdf,
    text_pdf,
)

OOXML = ["docx", "xlsx", "pptx"]
OOXML_MIME = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class TestOoxml:
    @pytest.mark.parametrize("ext", OOXML)
    def test_an_encrypted_office_file_is_found(self, ext: str) -> None:
        content = ENCRYPTED_BY_EXTENSION[ext]()

        assert has_encrypted_package(content)
        with pytest.raises(PasswordProtectedError) as raised:
            raise_if_password_protected_office(content, extension=ext)
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL

    @pytest.mark.parametrize("ext", OOXML)
    def test_it_is_found_by_mime_type_when_the_name_has_no_extension(self, ext: str) -> None:
        with pytest.raises(PasswordProtectedError):
            raise_if_password_protected_office(
                ENCRYPTED_BY_EXTENSION[ext](), extension="", mime_type=OOXML_MIME[ext]
            )

    @pytest.mark.parametrize("ext", OOXML)
    def test_a_plain_office_file_is_not(self, ext: str) -> None:
        content = PLAIN_BY_EXTENSION[ext]()

        assert not has_encrypted_package(content)
        raise_if_password_protected_office(content, extension=ext)

    def test_other_formats_are_left_alone(self) -> None:
        raise_if_password_protected_office(ENCRYPTED_BY_EXTENSION["docx"](), extension="txt")
        raise_if_password_protected_office("text", extension="docx")

    @pytest.mark.parametrize("shift", [9, 12], ids=["512-byte sectors", "4096-byte sectors"])
    def test_both_sector_sizes_are_read(self, shift: int) -> None:
        assert has_encrypted_package(cfbf(["EncryptionInfo", "EncryptedPackage"], sector_shift=shift))
        assert not has_encrypted_package(cfbf(["WordDocument", "1Table"], sector_shift=shift))

    def test_a_legacy_binary_office_file_is_not_called_encrypted(self) -> None:
        assert not has_encrypted_package(cfbf(["WordDocument", "1Table", "SummaryInformation"]))

    def test_the_stream_is_found_deep_in_a_long_directory(self) -> None:
        assert has_encrypted_package(cfbf([f"s{i}" for i in range(40)] + ["EncryptedPackage"]))


class TestMacroEnabled:
    @pytest.mark.parametrize("ext", ["docm", "xlsm", "pptm"])
    def test_an_encrypted_macro_enabled_file_is_found_by_extension(self, ext: str) -> None:
        with pytest.raises(PasswordProtectedError):
            raise_if_password_protected_office(ENCRYPTED_MACRO_BY_EXTENSION[ext](), extension=ext)

    @pytest.mark.parametrize("ext", ["docm", "xlsm", "pptm"])
    def test_and_by_mime_type(self, ext: str) -> None:
        with pytest.raises(PasswordProtectedError):
            raise_if_password_protected_office(
                ENCRYPTED_MACRO_BY_EXTENSION[ext](), extension="", mime_type=MACRO_MIME[ext]
            )

    def test_a_plain_macro_enabled_file_passes(self) -> None:
        raise_if_password_protected_office(PLAIN_BY_EXTENSION["docx"](), extension="docm")


class TestMalformedContainers:
    """A damaged container is never a crash or a hang, and never reads as encrypted."""

    def test_a_header_only_file(self) -> None:
        assert not has_encrypted_package(bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 504)

    def test_a_truncated_file(self) -> None:
        good = cfbf(["EncryptionInfo", "EncryptedPackage"])
        for cut in (7, 100, 511, 512, 700, len(good) - 1):
            assert has_encrypted_package(good[:cut]) in (True, False)

    def test_a_directory_chain_that_loops(self) -> None:
        started = time.monotonic()
        assert not has_encrypted_package(cfbf(["x"] * 30 + ["EncryptedPackage"], directory_loops=True))
        assert time.monotonic() - started < 0.5

    @pytest.mark.parametrize(
        "offset, fmt, value",
        [
            (30, "<H", 0),  # sector size
            (30, "<H", 31),
            (48, "<I", 0x7FFFFFF0),  # first directory sector far outside the file
            (48, "<I", 0xFFFFFFFA),
            (76, "<I", 0x7FFFFFF0),  # FAT sector outside the file
        ],
    )
    def test_a_header_that_points_outside_the_file(self, offset: int, fmt: str, value: int) -> None:
        damaged = bytearray(cfbf(["EncryptionInfo", "EncryptedPackage"]))
        struct.pack_into(fmt, damaged, offset, value)

        assert not has_encrypted_package(bytes(damaged))

    @pytest.mark.parametrize("first", [0, 1, 0xFFFFFFFE])
    def test_a_difat_chain_that_never_ends_is_bounded(self, first: int) -> None:
        damaged = bytearray(cfbf(["EncryptionInfo"]))
        struct.pack_into("<II", damaged, 68, first, 0xFFFFFFFF)
        started = time.monotonic()
        has_encrypted_package(bytes(damaged))
        assert time.monotonic() - started < 0.5

    def test_random_bytes_behind_the_magic(self) -> None:
        import random

        rng = random.Random(1234)
        for _ in range(200):
            body = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 4096)))
            assert has_encrypted_package(bytes.fromhex("D0CF11E0A1B11AE1") + body) in (True, False)

    def test_a_file_that_is_not_a_container(self) -> None:
        assert not has_encrypted_package(b"PK\x03\x04" + b"\0" * 600)
        assert not has_encrypted_package(b"")

    def test_only_the_header_and_directory_are_needed(self) -> None:
        # Everything after the directory is padding: cutting it off changes nothing.
        full = cfbf(["EncryptionInfo", "EncryptedPackage"]) + b"\xAB" * (4 * 1024 * 1024)
        head = full[:2048]

        assert has_encrypted_package(head) and has_encrypted_package(full)

    def test_cost_is_flat_in_the_size_of_the_file(self) -> None:
        full = cfbf(["EncryptionInfo", "EncryptedPackage"]) + b"\xAB" * (50 * 1024 * 1024)
        has_encrypted_package(full)

        started = time.perf_counter()
        for _ in range(20):
            assert has_encrypted_package(full)
        per_call_ms = (time.perf_counter() - started) / 20 * 1000

        assert per_call_ms < 5, f"{per_call_ms:.2f} ms per 50 MB file"


class TestPdf:
    def test_a_pdf_that_needs_a_password_is_found(self) -> None:
        with pytest.raises(PasswordProtectedError) as raised:
            raise_if_pdf_needs_password(password_protected_pdf())
        assert MessageErrorClassifier.classify_by_exception(raised.value) == MessageErrorType.TERMINAL

    def test_an_owner_password_alone_is_not_a_password(self) -> None:
        raise_if_pdf_needs_password(owner_password_pdf())

    def test_a_plain_pdf_passes(self) -> None:
        raise_if_pdf_needs_password(text_pdf())

    @pytest.mark.parametrize("content", [b"", b"not a pdf", text_pdf()[:300]], ids=["empty", "text", "cut"])
    def test_a_pdf_pdfium_cannot_open_for_another_reason_is_not_a_password(self, content: bytes) -> None:
        raise_if_pdf_needs_password(content)

    def test_the_decision_is_pdfiums_status_not_its_wording(self) -> None:
        # A different error whose text says "password" is not a password error.
        assert not pdfium_password_error(pypdfium2.PdfiumError("Failed to load document (password mentioned)"))
        assert not pdfium_password_error(RuntimeError("Incorrect password error"))

    def test_an_error_without_pdfiums_code_is_not_a_password_even_after_a_password_failure(self) -> None:
        # pdfium's last-error state still says "password" here; an exception that carries no code must not read it.
        with pytest.raises(pypdfium2.PdfiumError):
            pypdfium2.PdfDocument(password_protected_pdf())
        assert not pdfium_password_error(pypdfium2.PdfiumError("Failed to load document"))
        try:
            pypdfium2.PdfDocument(password_protected_pdf())
        except pypdfium2.PdfiumError as exc:
            assert pdfium_password_error(exc)
