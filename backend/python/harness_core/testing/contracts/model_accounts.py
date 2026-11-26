"""Contract suite for ``ModelAccountStore`` (P2.4b).

Provide an ``account_harness`` fixture (``AccountHarness``): the store, the
``SecretSource`` the credential service reads the same accounts through, the
scope kind that source serves, and the manual clock both read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.access.errors import AccountInvalid
from harness_core.access.types import (
    ORG_OWNER,
    AccountKind,
    AccountRef,
    org_account_ref,
    user_account_ref,
)
from harness_core.credentials.errors import CredentialRevoked
from harness_core.credentials.types import MAX_TTL_S, CredentialScope

if TYPE_CHECKING:
    from harness_core.access.ports import ModelAccountStore
    from harness_core.credentials.ports import SecretSource
    from harness_core.testing.clock import ManualClock

TENANT = "org-1"
KEY = "sk-contract-0123456789abcdefWXYZ"
ROTATED = "sk-contract-rotated-9876543210ABCD"
ALICE = user_account_ref("user-a", "openAI")
BOB = user_account_ref("user-b", "openAI")
CLOUD = org_account_ref("acct-1")


class AccountHarness(Protocol):
    @property
    def store(self) -> ModelAccountStore: ...

    @property
    def secrets(self) -> SecretSource: ...

    @property
    def kind(self) -> str: ...

    @property
    def clock(self) -> ManualClock: ...


async def _put(h: AccountHarness, ref: AccountRef = ALICE, key: str = KEY, *, tenant: str = TENANT) -> str:
    account = await h.store.put(
        tenant, ref, "openAI", {"provider": "openAI", "apiKey": key}, secret_hint=key, label="mine", actor="user-a"
    )
    return account.version


def _scope(h: AccountHarness, ref: AccountRef) -> CredentialScope:
    return CredentialScope(h.kind, ref.account_id)


class ModelAccountStoreContract:
    @pytest.fixture
    def account_harness(self) -> AccountHarness:
        raise NotImplementedError("provide an account_harness fixture")

    async def test_ms1_put_answers_metadata_and_the_secret_resolves(self, account_harness: AccountHarness) -> None:
        h = account_harness
        account = await h.store.put(
            TENANT, ALICE, "openAI", {"provider": "openAI", "apiKey": KEY}, secret_hint=KEY, label="mine", actor="user-a"
        )
        assert (account.ref, account.owner, account.provider, account.label) == (ALICE, "user-a", "openAI", "mine")
        assert (account.last4, account.version, account.validation) == ("WXYZ", "1", "unvalidated")
        assert account.created_at == account.rotated_at == h.clock.now()
        assert KEY not in repr(account) and KEY not in repr(await h.store.account(TENANT, ALICE))
        secret = await h.secrets.read(TENANT, _scope(h, ALICE))
        assert secret is not None and secret.version == account.version
        assert secret.material == {"provider": "openAI", "apiKey": KEY}

    async def test_ms2_put_again_rotates_and_keeps_the_previous_version_for_the_retention(
        self, account_harness: AccountHarness
    ) -> None:
        h = account_harness
        first = await _put(h)
        created = h.clock.now()
        _ = h.clock.advance(30)
        rotated = await h.store.put(
            TENANT, ALICE, "openAI", {"provider": "openAI", "apiKey": ROTATED}, secret_hint=ROTATED, label=None, actor="user-a"
        )
        assert (rotated.version, rotated.last4, rotated.created_at, rotated.rotated_at) == ("2", "ABCD", created, h.clock.now())
        current = await h.secrets.read(TENANT, _scope(h, ALICE))
        old = await h.secrets.read(TENANT, _scope(h, ALICE), first)
        assert current is not None and current.material == {"provider": "openAI", "apiKey": ROTATED}
        assert old is not None and old.material == {"provider": "openAI", "apiKey": KEY}
        _ = h.clock.advance(MAX_TTL_S + 1)
        assert await h.secrets.read(TENANT, _scope(h, ALICE), first) is None

    async def test_ms3_lists_by_owner_and_tenant(self, account_harness: AccountHarness) -> None:
        h = account_harness
        _ = await _put(h, ALICE)
        _ = await _put(h, BOB)
        _ = await _put(h, CLOUD)
        assert [a.ref for a in await h.store.accounts(TENANT, "user-a")] == [ALICE]
        assert [a.ref for a in await h.store.accounts(TENANT, ORG_OWNER)] == [CLOUD]
        assert await h.store.accounts("org-2", "user-a") == ()
        assert await h.store.account("org-2", ALICE) is None
        assert await h.secrets.read("org-2", _scope(h, ALICE)) is None

    async def test_ms4_remove_revokes_every_version_at_once(self, account_harness: AccountHarness) -> None:
        h = account_harness
        first = await _put(h)
        _ = await _put(h, key=ROTATED)
        assert await h.store.remove(TENANT, ALICE, actor="user-a") is True
        assert await h.store.account(TENANT, ALICE) is None
        assert await h.store.accounts(TENANT, "user-a") == ()
        for version in (None, first):
            with pytest.raises(CredentialRevoked):
                _ = await h.secrets.read(TENANT, _scope(h, ALICE), version)
        assert await h.store.remove(TENANT, ALICE, actor="user-a") is False
        assert await h.store.remove(TENANT, BOB, actor="user-b") is False

    async def test_ms5_numbering_continues_after_a_removal(self, account_harness: AccountHarness) -> None:
        h = account_harness
        first = await _put(h)
        _ = await h.store.remove(TENANT, ALICE, actor="user-a")
        again = await _put(h, key=ROTATED)
        assert int(again) > int(first)
        assert await h.secrets.read(TENANT, _scope(h, ALICE), first) is None
        secret = await h.secrets.read(TENANT, _scope(h, ALICE))
        assert secret is not None and secret.material == {"provider": "openAI", "apiKey": ROTATED}

    async def test_ms6_erase_an_owner_or_a_tenant_leaves_nothing(self, account_harness: AccountHarness) -> None:
        h = account_harness
        for ref in (ALICE, BOB, CLOUD):
            _ = await _put(h, ref)
        _ = await _put(h, ALICE, tenant="org-2")
        assert await h.store.erase(TENANT, "user-a") == 1
        assert await h.secrets.read(TENANT, _scope(h, ALICE)) is None
        assert await h.store.account(TENANT, BOB) is not None
        assert await h.store.erase(TENANT) == 2
        assert await h.store.accounts(TENANT, ORG_OWNER) == ()
        assert await h.secrets.read(TENANT, _scope(h, CLOUD)) is None
        assert await h.store.account("org-2", ALICE) is not None

    @pytest.mark.parametrize(
        "make",
        [
            lambda: user_account_ref("../x", "openAI"),
            lambda: user_account_ref("user-a", "open/AI"),
            lambda: org_account_ref(""),
        ],
    )
    async def test_ms7_names_that_are_not_path_safe_are_refused(self, make: object) -> None:
        with pytest.raises(AccountInvalid):
            _ = make()  # type: ignore[operator]

    async def test_ms7_a_reference_of_the_wrong_shape_is_refused(self, account_harness: AccountHarness) -> None:
        with pytest.raises(AccountInvalid):
            _ = await account_harness.store.account(TENANT, AccountRef(AccountKind.USER_API_KEY, "cloud/acct-1"))


__all__ = ["AccountHarness", "ModelAccountStoreContract"]
