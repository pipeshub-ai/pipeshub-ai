from pathlib import Path

import pytest

from app.agents.openapi import attachments, service
from app.agents.openapi.store import ApiToolsetStore
from tests.support.in_memory_config import InMemoryConfigService

ACME_SPEC = (Path(__file__).resolve().parents[6] / "integration-tests" / "openapi_fixture" / "openapi.yaml").read_bytes()
PER_MEMBER, SHARED, OPEN = "a" * 32, "b" * 32, "c" * 32


@pytest.fixture
async def store() -> ApiToolsetStore:
    store = ApiToolsetStore(InMemoryConfigService())
    for instance_id, name, scheme, shared in ((PER_MEMBER, "Acme", "apiKeyAuth", False), (SHARED, "Shared", "apiKeyAuth", True),
                                              (OPEN, "Open", "none", False)):
        await store.save_instance(service.build_instance(
            service.InstanceInput(name=name, base_url="https://helpdesk.acme.test", operations=["list_tickets", "get_ticket"],
                                  spec=ACME_SPEC, auth_scheme_name=scheme, use_shared_credential=shared),
            instance_id=instance_id, org_id="org-1", user_id="admin-1",
        ))
    return store


async def _check(store: ApiToolsetStore, raw: object, *, service_account: bool = False, org: str = "org-1") -> list[str]:
    return await attachments.checked_attachments(store, org, raw, service_account=service_account)


async def test_valid_attachments_are_kept_once_in_order(store: ApiToolsetStore) -> None:
    raw = [f"{PER_MEMBER}:get_ticket", f"{OPEN}:list_tickets", f"{PER_MEMBER}:get_ticket"]
    assert await _check(store, raw) == [f"{PER_MEMBER}:get_ticket", f"{OPEN}:list_tickets"]


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("x", "must be a list"),
        (["list_tickets"], "is not an API action operation"),
        ([f"{PER_MEMBER}:bad name"], "is not an API action operation"),
        ([7], "is not an API action operation"),
        ([f"{'d' * 32}:list_tickets"], "doesn't exist"),
        ([f"{PER_MEMBER}:create_ticket"], "Acme has no operation create_ticket"),
    ],
)
async def test_bad_attachments_are_refused(store: ApiToolsetStore, raw: object, message: str) -> None:
    with pytest.raises(service.ApiToolsetError, match=message):
        await _check(store, raw)


async def test_another_orgs_toolset_does_not_exist(store: ApiToolsetStore) -> None:
    with pytest.raises(service.ApiToolsetError, match="doesn't exist"):
        await _check(store, [f"{PER_MEMBER}:list_tickets"], org="org-2")


async def test_too_many(store: ApiToolsetStore) -> None:
    with pytest.raises(service.ApiToolsetError, match="at most 500"):
        await _check(store, [f"{PER_MEMBER}:t{i}" for i in range(501)])


async def test_service_accounts_need_a_shared_or_no_credential(store: ApiToolsetStore) -> None:
    assert await _check(store, [f"{SHARED}:list_tickets", f"{OPEN}:list_tickets"], service_account=True)
    with pytest.raises(service.ApiToolsetError, match="shared credential"):
        await _check(store, [f"{PER_MEMBER}:list_tickets"], service_account=True)


def test_by_instance_groups_and_skips_malformed() -> None:
    entries = [f"{PER_MEMBER}:a", "junk", f"{OPEN}:b", f"{PER_MEMBER}:c"]
    assert attachments.by_instance(entries) == {PER_MEMBER: ["a", "c"], OPEN: ["b"]}
