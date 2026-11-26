import logging
import os
from logging import Logger

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.utils.encryption.secret_key_ring import platform_read_keys


# Custom error classes matching the Node.js implementation
class EncryptionError(Exception):
    def __init__(self, message, detail=None) -> None:
        super().__init__(f"{message}: {detail}" if detail else message)


class DecryptionError(Exception):
    def __init__(self, message, detail=None) -> None:
        super().__init__(f"{message}: {detail}" if detail else message)


class InvalidKeyFormatError(Exception):
    def __init__(self, message) -> None:
        super().__init__(message)


class EncryptionService:
    _instance = None

    def __init__(
        self, algorithm: str, secret_key: str, logger: Logger, read_keys: tuple[str, ...] | None = None
    ) -> None:
        # In this example, algorithm should be "aes-256-gcm"
        self.algorithm = algorithm
        self.secret_key = secret_key  # this is a hex string
        self.logger = logger
        # Every key ``decrypt`` tries, the writer's first (ADR-KEY-01).
        self.read_keys = read_keys or (secret_key,)

    @classmethod
    def get_instance(cls, algorithm: str, secret_key: str, logger: Logger) -> "EncryptionService":
        """The platform cipher. Given the deployment's own key it also reads under the ring's other key
        (``SECRET_KEY_PREVIOUS`` or ``SECRET_KEY_NEXT``); it writes only under ``secret_key``."""
        if cls._instance is None:
            keys = platform_read_keys(secret_key)
            # Given the old SHA-256 key of the deployment's secret, it still writes under the HKDF one (SEC-D-05).
            cls._instance = EncryptionService(algorithm, keys[0], logger, keys)
        return cls._instance

    def encrypt(self, text: str) -> str:
        try:
            # Recommended IV length for GCM is 12 bytes
            iv = os.urandom(12)
            key = bytes.fromhex(self.secret_key)
            aesgcm = AESGCM(key)
            # Encrypt returns ciphertext with the tag appended (last 16 bytes)
            encrypted = aesgcm.encrypt(iv, text.encode("utf-8"), None)
            # Split ciphertext and auth tag
            ciphertext = encrypted[:-16]
            auth_tag = encrypted[-16:]
            # Return the encrypted string as "iv:ciphertext:authTag"
            return f"{iv.hex()}:{ciphertext.hex()}:{auth_tag.hex()}"
        except Exception as e:
            self.logger.error("Encryption failed", exc_info=True)
            raise EncryptionError("Encryption failed", str(e))

    def decrypt(self, encrypted_text: str) -> str:
        return self.decrypt_detailed(encrypted_text)[0]

    def decrypt_detailed(self, encrypted_text: str) -> tuple[str, bool]:
        """The plaintext, and whether the writer's key opened it (False: the ring's other key did)."""
        if encrypted_text is None:
            raise DecryptionError("Decryption failed, encrypted text is None")
        try:
            # For AES-256-GCM, expect format "iv:ciphertext:authTag"
            parts = encrypted_text.split(":")
            PARTS_LENGTH = 3
            if len(parts) != PARTS_LENGTH:
                raise InvalidKeyFormatError(
                    "Invalid encrypted text format; expected format iv:ciphertext:authTag"
                )
            iv_hex, ciphertext_hex, auth_tag_hex = parts
            iv = bytes.fromhex(iv_hex)
            ciphertext = bytes.fromhex(ciphertext_hex)
            auth_tag = bytes.fromhex(auth_tag_hex)
            # Recombine ciphertext and auth tag as expected by AESGCM.decrypt
            combined = ciphertext + auth_tag
            for index, key in enumerate(self.read_keys):
                try:
                    # GCM authenticates: under another key this raises, it never decrypts to garbage.
                    decrypted = AESGCM(bytes.fromhex(key)).decrypt(iv, combined, None)
                except InvalidTag:
                    continue
                return decrypted.decode("utf-8"), index == 0
            raise InvalidTag
        except Exception as e:
            self.logger.error("Decryption failed", exc_info=True)
            raise DecryptionError(
                "Decryption failed, could be due to different encryption algorithm or secret key",
                str(e),
            )


# Example usage:
if __name__ == "__main__":
    # Replace these with your actual values.
    logger = logging.getLogger("Encryption Service")
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    secret_key = ""
    service = EncryptionService.get_instance("aes-256-gcm", secret_key, logger)

    # Decrypt the message back
    try:
        encrypted = ""
        decrypted = service.decrypt(encrypted)
        print("Decrypted:", decrypted)
    except Exception as error:
        print(error)
