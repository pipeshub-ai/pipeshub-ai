import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# Custom error classes matching the Node.js implementation
class EncryptionError(Exception):
    def __init__(self, message, detail=None):
        super().__init__(f"{message}: {detail}" if detail else message)


class DecryptionError(Exception):
    def __init__(self, message, detail=None):
        super().__init__(f"{message}: {detail}" if detail else message)


class InvalidKeyFormatError(Exception):
    def __init__(self, message):
        super().__init__(message)


# Setup logger to mimic Logger service in Node.js
logger = logging.getLogger("Encryption Service")
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)


class EncryptionService:
    _instance = None

    def __init__(self, algorithm: str, secret_key: str):
        # In this example, algorithm should be "aes-256-gcm"
        self.algorithm = algorithm
        self.secret_key = secret_key  # this is a hex string

    @classmethod
    def get_instance(cls, algorithm: str, secret_key: str):
        if cls._instance is None:
            cls._instance = EncryptionService(algorithm, secret_key)
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
            logger.error("Encryption failed", exc_info=True)
            raise EncryptionError("Encryption failed", str(e))

    def decrypt(self, encrypted_text: str) -> str:
        if encrypted_text is None:
            raise DecryptionError("Decryption failed, encrypted text is None")
        try:
            # For AES-256-GCM, expect format "iv:ciphertext:authTag"
            parts = encrypted_text.split(":")
            if len(parts) != 3:
                raise InvalidKeyFormatError(
                    "Invalid encrypted text format; expected format iv:ciphertext:authTag"
                )
            iv_hex, ciphertext_hex, auth_tag_hex = parts
            iv = bytes.fromhex(iv_hex)
            ciphertext = bytes.fromhex(ciphertext_hex)
            auth_tag = bytes.fromhex(auth_tag_hex)
            key = bytes.fromhex(self.secret_key)
            # Recombine ciphertext and auth tag as expected by AESGCM.decrypt
            combined = ciphertext + auth_tag
            aesgcm = AESGCM(key)
            decrypted = aesgcm.decrypt(iv, combined, None)
            return decrypted.decode("utf-8")
        except Exception as e:
            logger.error("Decryption failed", exc_info=True)
            raise DecryptionError(
                "Decryption failed, could be due to different encryption algorithm or secret key",
                str(e),
            )


# Example usage:
if __name__ == "__main__":
    # Replace these with your actual values.
    secret_key = "47eabdd76483a14975e4f6349cbd6812992f9d10cdac3c388a147206e69bed3b"
    service = EncryptionService.get_instance("aes-256-gcm", secret_key)

    # Decrypt the message back
    try:
        encrypted = "f8585d7ec24d34e7824f3529:68a1bf96684e57524eea241aeaaa7eb4943428ae96502d2d2397dd6e38329f45ae28b26a6dcfb8f39142c3b4640e0bb2a56949f293c43545e7ce808cbd78dd959338e62bce80f7bfba0fee45437e31957d9d9a1ba477f4182053fae7686d506a1092789506ec607e4eaf2fda068923313b2fb635ab7965eccb194fe4372f8d52fa969123df28fe14a827b4a9f338262383fb34c144a644148ba173df70ba117398cdb38b368bbebfe0d0ecbe8f3e6f66d47ccbbbee82988569c900f3f3dd763819aa59a87a720550663a70d8eac592bba4bf90ebbca5bdec3f93d72f06f4b6bf276672536ef09efca831552fd63c0e0f309aa5a9f8:b5224d1b58a496246fb850e51c1ae291"
        decrypted = service.decrypt(encrypted)
        print("Decrypted:", decrypted)
    except Exception as error:
        print(error)
