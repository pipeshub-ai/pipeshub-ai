"""SEC-D-05: SECRET_KEY strength and the platform cipher's key derivation (ADR-KEY-02).

The strength cases are shared with Node (``contracts/secret-key/strength-cases.json``), so the two rings refuse alike.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest

from app.utils.encryption.encryption_service import EncryptionService
from app.utils.encryption.secret_key_ring import (
    SecretKeyRingError,
    hashed_secret,
    platform_cipher_key,
    secret_key_ring,
    weak_secret_reason,
)

REPO = Path(__file__).resolve().parents[6]
CASES = json.loads((REPO / "contracts" / "secret-key" / "strength-cases.json").read_text(encoding="utf-8"))["cases"]
STRONG = hashlib.sha256(b"sec-d-05 strong").hexdigest()
OTHER = hashlib.sha256(b"sec-d-05 other").hexdigest()
_LOG = logging.getLogger("test")


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_strength_cases(case: dict[str, object]) -> None:
    assert weak_secret_reason(str(case["secret"])) == case["reason"]


def test_weak_key_refused_with_what_to_do_and_never_the_value() -> None:
    with pytest.raises(SecretKeyRingError) as caught:
        secret_key_ring({"SECRET_KEY": "your_random_encryption_secret_key"})
    message = str(caught.value)
    assert message.startswith("SECRET_KEY is a published placeholder")
    assert "openssl rand -base64 32" in message
    assert "SECRET_KEY_PREVIOUS" in message
    assert "your_random_encryption_secret_key" not in message


def test_weak_next_key_refused() -> None:
    with pytest.raises(SecretKeyRingError, match=r"^SECRET_KEY_NEXT is a published placeholder"):
        secret_key_ring({"SECRET_KEY": STRONG, "SECRET_KEY_NEXT": "changeme"})


def test_weak_previous_key_accepted_so_an_install_can_move_off_it() -> None:
    ring = secret_key_ring({"SECRET_KEY": STRONG, "SECRET_KEY_PREVIOUS": "changeme"})
    assert ring.other_role == "previous"
    assert ring.cipher_keys({})[0] == platform_cipher_key(STRONG)


def test_platform_cipher_key_vector() -> None:
    # The same vector as Node's secret-key-strength.test.ts: both languages derive alike.
    assert platform_cipher_key(STRONG) == "3f9d988e2f25dc0fedd5e25858e5a67fbd3f6b2a3f77501607406e48fa940cec"
    assert platform_cipher_key(STRONG) != hashed_secret(STRONG)


def test_cipher_ring_order_and_rolling_upgrade_switch() -> None:
    ring = secret_key_ring({"SECRET_KEY": STRONG, "SECRET_KEY_PREVIOUS": OTHER})
    assert ring.cipher_keys({}) == (
        platform_cipher_key(STRONG), hashed_secret(STRONG), platform_cipher_key(OTHER), hashed_secret(OTHER),
    )
    assert ring.cipher_keys({"SECRET_KEY_KDF": " SHA256 "})[0] == hashed_secret(STRONG)


def test_legacy_sealed_value_opens_and_is_not_current() -> None:
    keys = secret_key_ring({"SECRET_KEY": STRONG}).cipher_keys({})
    legacy = EncryptionService("aes-256-gcm", hashed_secret(STRONG), _LOG).encrypt("stored")
    reader = EncryptionService("aes-256-gcm", keys[0], _LOG, keys)
    assert reader.decrypt_detailed(legacy) == ("stored", False)
    assert reader.decrypt_detailed(reader.encrypt("new")) == ("new", True)


def test_a_caller_passing_the_old_sha256_key_still_writes_under_hkdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", STRONG)
    monkeypatch.delenv("SECRET_KEY_PREVIOUS", raising=False)
    monkeypatch.delenv("SECRET_KEY_NEXT", raising=False)
    monkeypatch.delenv("SECRET_KEY_KDF", raising=False)
    monkeypatch.setattr(EncryptionService, "_instance", None)
    service = EncryptionService.get_instance("aes-256-gcm", hashed_secret(STRONG), _LOG)
    assert service.secret_key == platform_cipher_key(STRONG)
    monkeypatch.setattr(EncryptionService, "_instance", None)
