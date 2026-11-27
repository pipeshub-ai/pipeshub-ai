"""How the encrypted key-value store turns a value into stored text and back, per key.

The default cipher encrypts every value with the platform cipher (the install key and its read ring). An edition can return a cipher
of its own from ``app.edition_services.kv_cipher``, for instance one that picks a key per key prefix; it must still read what the
default wrote."""


from collections.abc import Callable


class InstallKeyCipher:
    """Every value under the platform cipher, read at each call (the store's cipher can be replaced, as a re-key does)."""

    def __init__(self, platform: Callable[[], object]) -> None:
        self.platform = platform

    def encrypt(self, key: str, text: str) -> str:
        return self.platform().encrypt(text)

    def decrypt(self, key: str, stored: str) -> str:
        return self.platform().decrypt(stored)
