"""Contract suite for ``CallSecrets`` (SEC4): what a holder resolved is what a call's outcome masks exactly.

Provide a ``call_secrets_harness`` fixture (``CallSecretsHarness``): the holder (``secrets``), ``resolve(material)``
(what the holder's boundary does when a client needs a credential record), and ``close()`` (the holder's teardown).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.security.token_patterns import SECRET_MASK

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.security.secrets import CallSecrets

CLIENT_SECRET = "contract-client-secret-4d1c0e"
ACCESS_TOKEN = "contract-access-token-9b72aa"
ROTATED = "contract-client-secret-rotated-77"
RECORD: JsonValue = {
    "isAuthenticated": True,
    "auth": {"baseUrl": "https://contract.example.test", "clientSecret": CLIENT_SECRET},
    "credentials": {"access_token": ACCESS_TOKEN, "token_type": "Bearer", "expires_at": "2026-10-10T00:00:00Z"},
}


class CallSecretsHarness(Protocol):
    @property
    def secrets(self) -> CallSecrets: ...

    async def resolve(self, material: JsonValue) -> None: ...

    async def close(self) -> None: ...


def _shown(harness: CallSecretsHarness, text: str) -> str:
    return harness.secrets.resolved_secrets().mask(text)


class CallSecretsContract:
    @pytest.fixture
    def call_secrets_harness(self) -> CallSecretsHarness:
        raise NotImplementedError("provide a call_secrets_harness fixture")

    async def test_cx1_nothing_resolved_masks_nothing(self, call_secrets_harness: CallSecretsHarness) -> None:
        assert not call_secrets_harness.secrets.resolved_secrets()
        assert _shown(call_secrets_harness, f"{CLIENT_SECRET} {ACCESS_TOKEN}") == f"{CLIENT_SECRET} {ACCESS_TOKEN}"

    async def test_cx2_a_resolved_records_secret_values_are_masked_and_its_plain_fields_are_not(
        self, call_secrets_harness: CallSecretsHarness
    ) -> None:
        await call_secrets_harness.resolve(RECORD)
        text = f"{CLIENT_SECRET} {ACCESS_TOKEN} https://contract.example.test Bearer 2026-10-10T00:00:00Z"
        assert _shown(call_secrets_harness, text) == (
            f"{SECRET_MASK} {SECRET_MASK} https://contract.example.test Bearer 2026-10-10T00:00:00Z"
        )
        assert CLIENT_SECRET not in repr(call_secrets_harness.secrets.resolved_secrets())

    async def test_cx3_every_record_resolved_since_counts(self, call_secrets_harness: CallSecretsHarness) -> None:
        await call_secrets_harness.resolve(RECORD)
        await call_secrets_harness.resolve({"auth": {"clientSecret": ROTATED}})
        assert _shown(call_secrets_harness, f"{CLIENT_SECRET} {ROTATED}") == f"{SECRET_MASK} {SECRET_MASK}"

    async def test_cx4_teardown_forgets_every_value(self, call_secrets_harness: CallSecretsHarness) -> None:
        await call_secrets_harness.resolve(RECORD)
        await call_secrets_harness.close()
        assert not call_secrets_harness.secrets.resolved_secrets()


__all__ = ["CallSecretsContract", "CallSecretsHarness"]
