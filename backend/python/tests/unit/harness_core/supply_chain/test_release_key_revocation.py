"""R41SUPPLY-FU-6 (ADR-SEC-06 D1): release keys expire on their own, and a signed list revokes them.

- RK-1: the shared vectors (``contracts/release-keys/revocation-vectors.json``, which Node runs too): which keys are
  usable at an instant, and which lists are applied or refused, and why. The file is what its generator writes.
- RK-2: every shipped key has a readable not-after at most three years after the key was made.
- RK-3: a key revoked by an applied list, or past its not-after, verifies no release data and roots no engine image;
  the list in force only grows, and the process keeps it whatever is applied later.
- RK-4: the refresher's fail-safe: the stored copy is in force at start; a newer fetched list is applied and stored;
  a fetch that fails or finds nothing, a list that is refused, and a store that is down all leave the list in force;
  with fetching off nothing is fetched; a long run without a fetch says so.

Every key here is generated; no release key is read or used.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.errors import VendorRulesInvalid
from harness_core.supply_chain.release_keys import (
    NO_REVOCATIONS,
    ReleaseKey,
    RevocationRefused,
    Revocations,
    format_instant,
    parse_instant,
    usable_keys,
    verify_revocations,
)
from harness_core.supply_chain.signatures import sign_detached
from harness_core.supply_chain.trust import ArtefactKind
from harness_engines import release_keys, trust_roots, vendor_rules
from tests.unit.harness_core.supply_chain.keys import new_key

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.unit.harness_core.supply_chain.keys import TestKey

REPO = Path(__file__).resolve().parents[6]
VECTORS = REPO / "contracts/release-keys/revocation-vectors.json"
GENERATOR = REPO / "scripts/engines/release_key_vectors.py"
NOW = datetime(2027, 1, 1, tzinfo=UTC)


def _vectors() -> dict[str, Any]:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def _keys(raw: dict[str, Any]) -> dict[str, ReleaseKey]:
    return {
        key_id: ReleaseKey(base64.b64decode(key["public"]), parse_instant(key["notAfter"]))  # type: ignore[arg-type]
        for key_id, key in raw.items()
    }


def _current(raw: dict[str, Any]) -> Revocations:
    issued = parse_instant(raw["issued"]) if raw["issued"] is not None else None
    return Revocations(issued, frozenset(raw["revoked"]))


def _bytes(value: str | None) -> bytes | None:
    return value.encode() if value is not None else None


@pytest.mark.parametrize("case", _vectors()["usable"], ids=lambda c: c["name"])
def test_rk1_usable_keys_vectors(case: dict[str, Any]) -> None:
    keys = _keys(_vectors()["keys"])
    now = parse_instant(case["now"])
    assert now is not None
    got = usable_keys(keys, Revocations(None, frozenset(case["revoked"])), now)
    assert sorted(got) == case["expect"]


@pytest.mark.parametrize("case", _vectors()["revocations"], ids=lambda c: c["name"])
def test_rk1_revocation_vectors(case: dict[str, Any]) -> None:
    keys = _keys(_vectors()["keys"])
    now = parse_instant(case["now"])
    assert now is not None
    expect = case["expect"]
    if "refused" in expect:
        with pytest.raises(RevocationRefused) as refused:
            _ = verify_revocations(_bytes(case["document"]), _bytes(case["signature"]), keys, _current(case["current"]),
                                   now)
        assert refused.value.reason == expect["refused"]
    else:
        got = verify_revocations(_bytes(case["document"]), _bytes(case["signature"]), keys, _current(case["current"]),
                                 now)
        assert (format_instant(got.issued) if got.issued else None, sorted(got.revoked)) == (
            expect["issued"], expect["revoked"])


def test_rk1_the_vectors_are_what_the_generator_writes() -> None:
    done = subprocess.run([sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, check=False,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert {"unknown_key", "expired_key", "revoked_signer", "bad_signature", "malformed", "stale", "future",
            "drops_revocation", "missing", "unsigned"} <= {c["expect"].get("refused") for c in _vectors()["revocations"]}


def test_rk2_every_release_key_expires_within_three_years_of_being_made() -> None:
    assert set(vendor_rules.RELEASE_KEY_NOT_AFTER) == set(vendor_rules.RELEASE_KEYS)
    for key_id, raw in vendor_rules.RELEASE_KEY_NOT_AFTER.items():
        not_after = parse_instant(raw)
        made = re.search(r"(\d{4}-\d{2}-\d{2})$", key_id)
        assert not_after is not None and made is not None, key_id
        created = datetime.strptime(made.group(1), "%Y-%m-%d").replace(tzinfo=UTC)
        assert created < not_after <= created + timedelta(days=3 * 366), key_id
    assert set(release_keys.release_key_set()) == set(vendor_rules.RELEASE_KEYS)


@pytest.fixture
def throwaway(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestKey, TestKey]]:
    """Two generated keys stand in for the release keys; the list in force starts empty and is reset after."""
    first, second = new_key("test-release-a"), new_key("test-release-b")
    monkeypatch.setattr(release_keys, "RELEASE_KEYS", {k.key_id: k.public for k in (first, second)})
    monkeypatch.setattr(release_keys, "RELEASE_KEY_NOT_AFTER", {
        first.key_id: "2099-01-01T00:00:00Z", second.key_id: "2099-01-01T00:00:00Z"})
    release_keys.reset_revocations_for_tests()
    vendor_rules._release.cache_clear()  # pyright: ignore[reportPrivateUsage]
    yield first, second
    release_keys.reset_revocations_for_tests()
    vendor_rules._release.cache_clear()  # pyright: ignore[reportPrivateUsage]


def _list(key: TestKey, issued: datetime, revoked: list[str]) -> tuple[bytes, bytes]:
    document = json.dumps({"schema": "pipeshub.revoked-keys/1", "issued": format_instant(issued),
                           "revoked": revoked}).encode()
    return document, sign_detached(document, key.private, key.key_id)


def _rules_signed_by(key: TestKey) -> tuple[bytes, bytes]:
    document = vendor_rules.packaged(vendor_rules.RULES_RESOURCE)
    assert document is not None
    return document, sign_detached(document, key.private, key.key_id)


def test_rk3_a_revoked_key_verifies_no_release_data_and_roots_no_image(throwaway: tuple[TestKey, TestKey]) -> None:
    first, second = throwaway
    rules = _rules_signed_by(first)
    assert vendor_rules.load_vendor_rules(*rules).ids()
    assert first.key_id in trust_roots.release_trust_roots().for_kind(ArtefactKind.ENGINE_IMAGE)
    now = datetime.now(UTC)
    applied = release_keys.apply_revocations(*_list(second, now, [first.key_id]))
    assert applied.revoked == {first.key_id}
    with pytest.raises(VendorRulesInvalid) as refused:
        _ = vendor_rules.load_vendor_rules(*rules)
    assert refused.value.detail == "unknown_key"
    assert dict(trust_roots.release_trust_roots().for_kind(ArtefactKind.ENGINE_IMAGE)) == {second.key_id: second.public}
    assert vendor_rules.load_vendor_rules(*_rules_signed_by(second)).ids()


def test_rk3_the_list_in_force_only_grows(throwaway: tuple[TestKey, TestKey]) -> None:
    first, second = throwaway
    now = datetime.now(UTC)
    _ = release_keys.apply_revocations(*_list(first, now - timedelta(hours=2), [first.key_id]))
    with pytest.raises(RevocationRefused) as old:
        _ = release_keys.apply_revocations(*_list(second, now - timedelta(hours=3), []))
    assert old.value.reason == "stale"
    with pytest.raises(RevocationRefused) as dropped:
        _ = release_keys.apply_revocations(*_list(second, now, []))
    assert dropped.value.reason == "drops_revocation"
    with pytest.raises(RevocationRefused) as by_revoked:
        _ = release_keys.apply_revocations(*_list(first, now, [first.key_id, second.key_id]))
    assert by_revoked.value.reason == "revoked_signer"
    assert release_keys.revocations().revoked == {first.key_id}
    assert set(release_keys.trusted_release_keys()) == {second.key_id}


def test_rk3_a_key_past_its_not_after_verifies_nothing(throwaway: tuple[TestKey, TestKey],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = throwaway
    monkeypatch.setattr(release_keys, "RELEASE_KEY_NOT_AFTER", {
        first.key_id: format_instant(datetime.now(UTC) - timedelta(seconds=1)), second.key_id: "2099-01-01T00:00:00Z"})
    assert set(release_keys.trusted_release_keys()) == {second.key_id}
    with pytest.raises(VendorRulesInvalid):
        _ = vendor_rules.load_vendor_rules(*_rules_signed_by(first))
    assert release_keys.trusted_release_keys(datetime(2098, 12, 31, tzinfo=UTC)) == {second.key_id: second.public}
    assert release_keys.trusted_release_keys(datetime(2099, 1, 1, tzinfo=UTC)) == {}


def test_rk3_the_shipped_rules_still_verify_with_nothing_revoked() -> None:
    release_keys.reset_revocations_for_tests()
    vendor_rules._release.cache_clear()  # pyright: ignore[reportPrivateUsage]
    assert release_keys.revocations() == NO_REVOCATIONS
    assert vendor_rules.release_vendor_rules().ids()


class FakeConfig:
    def __init__(self, stored: object = None, *, down: bool = False) -> None:
        self.stored = stored
        self.down = down
        self.writes: list[object] = []

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, *,
                         raise_on_error: bool = False) -> object:
        del default, use_cache
        assert raise_on_error
        assert key == "/services/harness/revokedReleaseKeys"
        if self.down:
            raise ConnectionError("store down")
        return self.stored

    async def set_config(self, key: str, value: object) -> bool:
        del key
        self.writes.append(value)
        self.stored = value
        return True


def _refresher(config: FakeConfig, url: str | None, answers: dict[str, bytes | None | Exception],
               clock: list[datetime] | None = None) -> tuple[Any, list[str]]:
    from app.harness.supply_chain.revocations import RevocationRefresher

    asked: list[str] = []

    async def fetch(target: str) -> bytes | None:
        asked.append(target)
        answer = answers.get(target)
        if isinstance(answer, Exception):
            raise answer
        return answer

    now = clock or [datetime.now(UTC)]
    return RevocationRefresher(config, url=url, fetch=fetch, clock=lambda: now[0]), asked  # type: ignore[arg-type]


async def test_rk4_the_stored_copy_is_in_force_at_start(throwaway: tuple[TestKey, TestKey]) -> None:
    first, second = throwaway
    document, signature = _list(second, datetime.now(UTC), [first.key_id])
    config = FakeConfig({"document": document.decode(), "signature": signature.decode()})
    refresher, asked = _refresher(config, None, {})
    await refresher.read_stored()
    assert release_keys.revocations().revoked == {first.key_id}
    assert asked == []


async def test_rk4_a_newer_fetched_list_is_applied_and_stored(throwaway: tuple[TestKey, TestKey]) -> None:
    first, second = throwaway
    url = "https://lists.example/revoked_keys.json"
    document, signature = _list(second, datetime.now(UTC), [first.key_id])
    config = FakeConfig()
    refresher, asked = _refresher(config, url, {url: document, f"{url}.sig": signature})
    await refresher.refresh_once()
    assert asked == [url, f"{url}.sig"]
    assert release_keys.revocations().revoked == {first.key_id}
    assert config.writes == [{"document": document.decode(), "signature": signature.decode()}]
    await refresher.refresh_once()
    assert len(config.writes) == 1, "a list already in force is not stored again"


@pytest.mark.parametrize("failure", ["unreachable", "absent", "forged", "store_down"])
async def test_rk4_whatever_fails_the_list_in_force_stays(throwaway: tuple[TestKey, TestKey], failure: str) -> None:
    first, second = throwaway
    url = "https://lists.example/revoked_keys.json"
    _ = release_keys.apply_revocations(*_list(second, datetime.now(UTC) - timedelta(hours=1), [first.key_id]))
    forged_doc, _sig = _list(second, datetime.now(UTC), [])
    answers: dict[str, bytes | None | Exception] = {
        "unreachable": {url: ConnectionError("no route")},
        "absent": {url: None},
        "forged": {url: forged_doc, f"{url}.sig": sign_detached(forged_doc, new_key("test-other").private,
                                                                second.key_id)},
        "store_down": {url: ConnectionError("no route")},
    }[failure]
    config = FakeConfig(down=failure == "store_down")
    refresher, _ = _refresher(config, url, answers)
    await refresher.refresh_once()
    assert release_keys.revocations().revoked == {first.key_id}
    assert config.writes == []


async def test_rk4_fetching_off_fetches_nothing_and_a_long_silence_is_logged(
    throwaway: tuple[TestKey, TestKey], caplog: pytest.LogCaptureFixture
) -> None:
    del throwaway
    from app.harness.supply_chain.revocations import DEFAULT_URL, revocations_url

    assert revocations_url({}) == DEFAULT_URL
    assert revocations_url({"PIPESHUB_REVOKED_KEYS_URL": ""}) is None
    assert revocations_url({"PIPESHUB_REVOKED_KEYS_URL": "off"}) is None
    refresher, asked = _refresher(FakeConfig(), None, {})
    await refresher.refresh_once()
    assert asked == []
    url = "https://lists.example/revoked_keys.json"
    clock = [datetime.now(UTC)]
    refresher, _ = _refresher(FakeConfig(), url, {url: ConnectionError("offline")}, clock)
    clock[0] += timedelta(days=8)
    with caplog.at_level("WARNING"):
        await refresher.refresh_once()
    assert "release_keys.revocations_stale" in caplog.text
