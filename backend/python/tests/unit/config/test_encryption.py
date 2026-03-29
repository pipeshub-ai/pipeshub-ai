"""Tests for app.config.encryption.encryption_service.EncryptionService"""

import logging

import pytest

from app.config.encryption.encryption_service import (
    DecryptionError,
    EncryptionError,
    EncryptionService,
    InvalidKeyFormatError,
)

# A valid 256-bit (32-byte) hex key for AES-256-GCM
TEST_SECRET_KEY = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the singleton before each test to avoid cross-test pollution."""
    EncryptionService._instance = None
    yield
    EncryptionService._instance = None


@pytest.fixture
def logger():
    return logging.getLogger("test-encryption")


@pytest.fixture
def service(logger):
    return EncryptionService("aes-256-gcm", TEST_SECRET_KEY, logger)


# ---------------------------------------------------------------------------
# encrypt / decrypt roundtrip
# ---------------------------------------------------------------------------


class TestEncryptDecryptRoundtrip:
    """Encrypt then decrypt should return the original plaintext."""

    def test_roundtrip_simple(self, service):
        plaintext = "hello world"
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_empty_string(self, service):
        plaintext = ""
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_unicode(self, service):
        plaintext = "Unicode test: \u00e9\u00e0\u00fc \u4f60\u597d"
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_emoji(self, service):
        plaintext = "Emoji: \U0001f600 \U0001f680"
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_long_text(self, service):
        plaintext = "A" * 10000
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext

    def test_roundtrip_special_characters(self, service):
        plaintext = "colons:in:the:text and {json: true}"
        ciphertext = service.encrypt(plaintext)
        assert service.decrypt(ciphertext) == plaintext


# ---------------------------------------------------------------------------
# Ciphertext randomness (unique IV each time)
# ---------------------------------------------------------------------------


class TestEncryptRandomness:
    """Each encryption produces different ciphertext due to random IV."""

    def test_different_ciphertext_each_time(self, service):
        plaintext = "same message"
        ct1 = service.encrypt(plaintext)
        ct2 = service.encrypt(plaintext)
        assert ct1 != ct2, "Encrypting the same text twice should produce different ciphertexts"

    def test_ciphertext_format_iv_ct_tag(self, service):
        """Ciphertext has format iv:ciphertext:authTag (three hex parts)."""
        ct = service.encrypt("test")
        parts = ct.split(":")
        assert len(parts) == 3, "Ciphertext should have exactly 3 colon-separated parts"
        # Each part should be valid hex
        for part in parts:
            int(part, 16)  # raises ValueError if not hex

    def test_iv_length_is_12_bytes(self, service):
        """IV should be 12 bytes (24 hex characters)."""
        ct = service.encrypt("test")
        iv_hex = ct.split(":")[0]
        assert len(iv_hex) == 24  # 12 bytes = 24 hex chars

    def test_auth_tag_length_is_16_bytes(self, service):
        """Auth tag should be 16 bytes (32 hex characters)."""
        ct = service.encrypt("test")
        tag_hex = ct.split(":")[2]
        assert len(tag_hex) == 32  # 16 bytes = 32 hex chars


# ---------------------------------------------------------------------------
# Decryption failure cases
# ---------------------------------------------------------------------------


class TestDecryptionFailures:
    """Tampered or malformed ciphertext should raise DecryptionError."""

    def test_tampered_ciphertext_raises_error(self, service):
        ct = service.encrypt("secret data")
        parts = ct.split(":")
        # Flip a nibble in the ciphertext portion
        tampered = list(parts[1])
        tampered[0] = "f" if tampered[0] != "f" else "0"
        parts[1] = "".join(tampered)
        tampered_ct = ":".join(parts)

        with pytest.raises(DecryptionError):
            service.decrypt(tampered_ct)

    def test_tampered_auth_tag_raises_error(self, service):
        ct = service.encrypt("secret data")
        parts = ct.split(":")
        # Flip a nibble in the auth tag
        tampered = list(parts[2])
        tampered[0] = "a" if tampered[0] != "a" else "b"
        parts[2] = "".join(tampered)
        tampered_ct = ":".join(parts)

        with pytest.raises(DecryptionError):
            service.decrypt(tampered_ct)

    def test_tampered_iv_raises_error(self, service):
        ct = service.encrypt("secret data")
        parts = ct.split(":")
        tampered = list(parts[0])
        tampered[0] = "c" if tampered[0] != "c" else "d"
        parts[0] = "".join(tampered)
        tampered_ct = ":".join(parts)

        with pytest.raises(DecryptionError):
            service.decrypt(tampered_ct)

    def test_wrong_format_two_parts_raises_error(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("aabbccdd:eeffaabb")

    def test_wrong_format_single_string_raises_error(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("just_a_random_string")

    def test_wrong_format_four_parts_raises_error(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("aa:bb:cc:dd")

    def test_none_input_raises_error(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt(None)

    def test_empty_string_raises_error(self, service):
        with pytest.raises(DecryptionError):
            service.decrypt("")

    def test_wrong_key_raises_error(self, logger):
        """Decrypting with a different key should fail."""
        service1 = EncryptionService(
            "aes-256-gcm", TEST_SECRET_KEY, logger
        )
        other_key = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        service2 = EncryptionService("aes-256-gcm", other_key, logger)

        ct = service1.encrypt("my secret")
        with pytest.raises(DecryptionError):
            service2.decrypt(ct)


# ---------------------------------------------------------------------------
# Singleton pattern
# ---------------------------------------------------------------------------


class TestSingleton:
    """get_instance() should return the same object after initialisation."""

    def test_get_instance_returns_same_object(self, logger):
        inst1 = EncryptionService.get_instance("aes-256-gcm", TEST_SECRET_KEY, logger)
        inst2 = EncryptionService.get_instance("aes-256-gcm", TEST_SECRET_KEY, logger)
        assert inst1 is inst2

    def test_get_instance_ignores_new_args_after_first(self, logger):
        """Once created, subsequent calls return the existing instance regardless of args."""
        inst1 = EncryptionService.get_instance("aes-256-gcm", TEST_SECRET_KEY, logger)
        other_key = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        inst2 = EncryptionService.get_instance("aes-256-gcm", other_key, logger)
        assert inst1 is inst2
        assert inst2.secret_key == TEST_SECRET_KEY

    def test_get_instance_creates_when_none(self, logger):
        """get_instance creates a new instance when _instance is None."""
        assert EncryptionService._instance is None
        inst = EncryptionService.get_instance("aes-256-gcm", TEST_SECRET_KEY, logger)
        assert inst is not None
        assert EncryptionService._instance is inst
        assert inst.algorithm == "aes-256-gcm"
        assert inst.secret_key == TEST_SECRET_KEY

    def test_get_instance_returns_existing_after_direct_init(self, logger):
        """If _instance was set by direct __init__ assignment, get_instance still uses singleton."""
        # Manually create and assign an instance
        direct = EncryptionService("aes-256-gcm", TEST_SECRET_KEY, logger)
        EncryptionService._instance = direct
        # get_instance should return the same instance
        inst = EncryptionService.get_instance("aes-256-gcm", "other_key_ignored", logger)
        assert inst is direct


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class TestExceptionClasses:
    """Tests for custom exception classes."""

    def test_encryption_error_with_detail(self):
        err = EncryptionError("Encryption failed", "bad key")
        assert "Encryption failed" in str(err)
        assert "bad key" in str(err)

    def test_encryption_error_without_detail(self):
        err = EncryptionError("Encryption failed")
        assert str(err) == "Encryption failed"

    def test_decryption_error_with_detail(self):
        err = DecryptionError("Decryption failed", "wrong format")
        assert "Decryption failed" in str(err)
        assert "wrong format" in str(err)

    def test_decryption_error_without_detail(self):
        err = DecryptionError("Decryption failed")
        assert str(err) == "Decryption failed"

    def test_invalid_key_format_error(self):
        err = InvalidKeyFormatError("Invalid key format")
        assert str(err) == "Invalid key format"

    def test_encryption_error_is_exception(self):
        assert issubclass(EncryptionError, Exception)

    def test_decryption_error_is_exception(self):
        assert issubclass(DecryptionError, Exception)

    def test_invalid_key_format_error_is_exception(self):
        assert issubclass(InvalidKeyFormatError, Exception)


# ---------------------------------------------------------------------------
# Encryption failure cases
# ---------------------------------------------------------------------------


class TestEncryptionFailures:
    """Tests for encrypt() failure paths."""

    def test_encrypt_with_bad_key_raises_encryption_error(self, logger):
        """Encrypt with an invalid hex key raises EncryptionError."""
        svc = EncryptionService("aes-256-gcm", "not-a-valid-hex-key", logger)
        with pytest.raises(EncryptionError):
            svc.encrypt("hello")
