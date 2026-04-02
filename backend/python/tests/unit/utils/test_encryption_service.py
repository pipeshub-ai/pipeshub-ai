"""Tests for app.utils.encryption.encryption_service.EncryptionService.

This is a mirror of config/test_encryption.py but targets the utils path
to ensure coverage is reported for app.utils.encryption.encryption_service.
"""

import logging

import pytest

from app.utils.encryption.encryption_service import (
    DecryptionError,
    EncryptionError,
    EncryptionService,
    InvalidKeyFormatError,
)

# A valid 256-bit (32-byte) hex key for AES-256-GCM
TEST_SECRET_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton before each test."""
    EncryptionService._instance = None
    yield
    EncryptionService._instance = None


@pytest.fixture
def logger():
    return logging.getLogger("test-encryption-utils")


@pytest.fixture
def service(logger):
    return EncryptionService("aes-256-gcm", TEST_SECRET_KEY, logger)


class TestEncryptDecryptRoundtrip:
    def test_roundtrip_simple(self, service):
        plaintext = "hello world"
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_empty_string(self, service):
        plaintext = ""
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_unicode(self, service):
        plaintext = "Unicode test: \u00e9\u00e0\u00fc"
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_long_text(self, service):
        plaintext = "A" * 10000
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext


class TestEncryptRandomness:
    def test_different_ciphertext_each_time(self, service):
        ct1 = service.encrypt("same")
        ct2 = service.encrypt("same")
        assert ct1 != ct2

    def test_ciphertext_format(self, service):
        ct = service.encrypt("test")
        parts = ct.split(":")
        assert len(parts) == 3
        for part in parts:
            int(part, 16)

    def test_iv_length(self, service):
        ct = service.encrypt("test")
        iv_hex = ct.split(":")[0]
        assert len(iv_hex) == 24

    def test_auth_tag_length(self, service):
        ct = service.encrypt("test")
        tag_hex = ct.split(":")[2]
        assert len(tag_hex) == 32


class TestDecryptionFailures:
    def test_tampered_ciphertext(self, service):
        ct = service.encrypt("secret")
        parts = ct.split(":")
        tampered = list(parts[1])
        tampered[0] = "f" if tampered[0] != "f" else "0"
        parts[1] = "".join(tampered)
        with pytest.raises(DecryptionError):
            service.decrypt(":".join(parts))

    def test_wrong_format_two_parts(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("aabb:ccdd")

    def test_single_string(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("random_string")

    def test_four_parts(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("aa:bb:cc:dd")

    def test_none_input(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt(None)

    def test_empty_string(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("")

    def test_wrong_key(self, logger):
        svc1 = EncryptionService("aes-256-gcm", TEST_SECRET_KEY, logger)
        other_key = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        svc2 = EncryptionService("aes-256-gcm", other_key, logger)
        ct = svc1.encrypt("secret")
        with pytest.raises(DecryptionError):
            svc2.decrypt(ct)


class TestSingleton:
    def test_get_instance_returns_same_object(self, logger):
        i1 = EncryptionService.get_instance("aes-256-gcm", TEST_SECRET_KEY, logger)
        i2 = EncryptionService.get_instance("aes-256-gcm", TEST_SECRET_KEY, logger)
        assert i1 is i2

    def test_get_instance_creates_when_none(self, logger):
        assert EncryptionService._instance is None
        inst = EncryptionService.get_instance("aes-256-gcm", TEST_SECRET_KEY, logger)
        assert inst is not None
        assert inst.algorithm == "aes-256-gcm"
        assert inst.secret_key == TEST_SECRET_KEY


class TestExceptionClasses:
    def test_encryption_error_with_detail(self):
        err = EncryptionError("msg", "detail")
        assert "msg" in str(err)
        assert "detail" in str(err)

    def test_encryption_error_without_detail(self):
        err = EncryptionError("msg")
        assert str(err) == "msg"

    def test_decryption_error_with_detail(self):
        err = DecryptionError("msg", "detail")
        assert "msg" in str(err)
        assert "detail" in str(err)

    def test_decryption_error_without_detail(self):
        err = DecryptionError("msg")
        assert str(err) == "msg"

    def test_invalid_key_format_error(self):
        err = InvalidKeyFormatError("bad key")
        assert str(err) == "bad key"


class TestEncryptionFailures:
    def test_encrypt_with_bad_key(self, logger):
        svc = EncryptionService("aes-256-gcm", "not-hex", logger)
        with pytest.raises(EncryptionError):
            svc.encrypt("hello")
