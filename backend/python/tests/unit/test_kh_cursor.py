"""The paging cursor round-trips, binds to its issuer, and never repairs itself.

The rule these enforce: a cursor that is malformed, forged, or someone else's
raises, and the caller answers 400. Every alternative — clamping to the first
page, ignoring the unreadable half, treating an unknown version as current —
turns a bug or an attack into a result the user cannot tell from a real one.
"""

import base64
import json
import time

import pytest

from app.utils.kh_cursor import (
    Boundary,
    CursorError,
    KnowledgeHubCursor,
    LivePartitions,
    decode,
    derive_cursor_secret,
    encode,
)

SECRET = "test-secret"


def _boundary(**overrides) -> Boundary:
    fields = {"null_rank": 0, "sort_key": "report.pdf", "last_id": "rec-9"}
    fields.update(overrides)
    return Boundary(**fields)


def _cursor(**overrides) -> KnowledgeHubCursor:
    fields = {
        "boundary": _boundary(),
        "live": LivePartitions.of(["rg-1", "rg-2"], {"rg-1"}),
        "items_seen": 20,
        "user_id": "user-u",
        "org_id": "org-1",
        "issued_at_ms": int(time.time() * 1000),
    }
    fields.update(overrides)
    return KnowledgeHubCursor(**fields)


def _tamper(token: str, mutate) -> str:
    payload, _, signature = token.partition(".")
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    wire = json.loads(raw)
    mutate(wire)
    edited = json.dumps(wire, separators=(",", ":"), sort_keys=True).encode()
    rebuilt = base64.urlsafe_b64encode(edited).decode().rstrip("=")
    return f"{rebuilt}.{signature}" if signature else rebuilt


def test_round_trips_every_field() -> None:
    original = _cursor(
        live=LivePartitions.of(["rg-1", "kb-1", "__apps__"], {"rg-1", "__apps__"}),
        direction="prev",
        total=142,
        counts_by_type={"FILE": 100, "MAIL": 42},
        filters={"recordTypes": ["FILE"]},
        sort_by="name",
        sort_order="desc",
        parent_id="rg-1",
        parent_type="RECORD_GROUP",
        via_parent_id="rg-2",
        mode="browse",
    )
    assert decode(encode(original, SECRET), SECRET) == original


def test_a_null_sort_key_survives_the_round_trip() -> None:
    """A null sort key is a real boundary, not a missing field.

    Records with no size sort together at one end; resuming there must not be
    confused with "no cursor", or the page restarts from the top.
    """
    token = encode(_cursor(boundary=_boundary(sort_key=None, null_rank=1)), SECRET)
    restored = decode(token, SECRET)
    assert restored.boundary.sort_key is None
    assert restored.boundary.null_rank == 1


def test_the_boundary_resumes_in_the_providers_shape() -> None:
    row = {"id": "rec-9", "sortKey": "report.pdf", "nullRank": 0, "name": "Report"}
    assert Boundary.of(row).as_after() == {"nullRank": 0, "sortKey": "report.pdf", "id": "rec-9"}


def test_only_partitions_still_in_play_are_selected() -> None:
    """A finished partition is not queried again."""
    partitions = ["__apps__", "rg-1", "rg-2", "kb-1"]
    live = LivePartitions.of(partitions, {"rg-1", "kb-1"})
    assert decode(encode(_cursor(live=live), SECRET), SECRET).live.select(partitions) == [
        "rg-1", "kb-1",
    ]


def test_a_changed_partition_list_falls_back_to_querying_them_all() -> None:
    """Positions only mean something against the list they
    were recorded from.

    Without the fingerprint, a partition the user gained access to shifts every
    later bit by one — silently skipping a whole partition's results while the
    page still looks full. Querying them all is exact, only less selective.
    """
    partitions = ["__apps__", "rg-1", "rg-2"]
    live = LivePartitions.of(partitions, {"rg-2"})
    assert live.select(partitions) == ["rg-2"]
    assert live.select(["__apps__", "rg-0", "rg-1", "rg-2"]) is None
    assert live.select(["__apps__", "rg-1"]) is None, "a lost partition also shifts positions"


def test_the_cursor_stays_small_when_the_partitions_are_many() -> None:
    """Thousands of top-level groups: a Drive tenant has one per user, and
    the cursor rides in a URL.

    Naming the live partitions by id is what this encoding exists to avoid: at
    a few thousand partitions that list alone runs past the ~8 KB many proxies
    allow, and paging breaks at exactly the scale where skipping finished
    partitions is worth anything.
    """
    partitions = [f"rg-{index}" for index in range(5000)]
    token = encode(_cursor(live=LivePartitions.of(partitions, set(partitions))), SECRET)
    named = len(json.dumps(partitions))
    assert len(token) < named // 10, f"{len(token)} vs {named} naming them"
    assert len(token) < 2000, len(token)
    assert decode(token, SECRET).live.select(partitions) == partitions


def test_a_previous_page_cursor_carries_no_partition_set() -> None:
    """A partition exhausted going forward can still hold rows behind the boundary."""
    restored = decode(encode(_cursor(live=None, direction="prev"), SECRET), SECRET)
    assert restored.live is None


@pytest.mark.parametrize(
    "token",
    ["", "not-base64!!", "····", "YWJj", base64.urlsafe_b64encode(b"[]").decode()],
)
def test_garbage_raises_rather_than_falling_back(token: str) -> None:
    """A cursor that cannot be read is a 400, never a silent first page."""
    with pytest.raises(CursorError):
        decode(token)


@pytest.mark.parametrize("token", ["····", "····.YWJj", "YWJj.····", "é.é"])
def test_a_non_ascii_cursor_is_refused_when_signing_is_configured(token: str) -> None:
    """The signature is computed over the payload's bytes, and a payload that
    has none in ASCII must be refused as a cursor, not escape as a server error."""
    with pytest.raises(CursorError):
        decode(token, SECRET)


@pytest.mark.parametrize("version", [1, 99])
def test_another_version_is_refused(version: int) -> None:
    """A version-1 cursor carried per-partition boundaries; reading one as a shared
    boundary would resume every partition from the wrong place."""
    token = _tamper(encode(_cursor()), lambda w: w.__setitem__("v", version))
    with pytest.raises(CursorError, match="version"):
        decode(token)


def test_a_cursor_without_a_boundary_is_refused() -> None:
    token = _tamper(encode(_cursor()), lambda w: w.pop("b"))
    with pytest.raises(CursorError, match="no boundary"):
        decode(token)


def test_a_boundary_missing_its_id_is_refused() -> None:
    token = _tamper(encode(_cursor()), lambda w: w["b"].pop("id"))
    with pytest.raises(CursorError, match="missing 'id'"):
        decode(token)


def test_a_bool_null_rank_is_refused() -> None:
    """`True` is an int in Python and would order where no real rank does."""
    token = _tamper(encode(_cursor()), lambda w: w["b"].__setitem__("nr", True))
    with pytest.raises(CursorError, match="null rank"):
        decode(token)


@pytest.mark.parametrize("broken", [["rg-1"], {"h": "abc"}, {"h": 1, "b": "AA"}])
def test_a_malformed_partition_set_is_refused(broken) -> None:
    token = _tamper(encode(_cursor()), lambda w: w.__setitem__("p", broken))
    with pytest.raises(CursorError, match="live partitions"):
        decode(token)


def test_a_tampered_payload_fails_the_signature() -> None:
    """Editing the payload to reach another user's scope is refused
    at the signature, before any value is trusted."""
    forged = _tamper(
        encode(_cursor(), SECRET), lambda w: w.__setitem__("u", "user-v")
    )
    with pytest.raises(CursorError, match="signature"):
        decode(forged, SECRET)


def test_an_unsigned_cursor_is_refused_when_signing_is_configured() -> None:
    """Otherwise stripping the signature is enough to edit the payload."""
    with pytest.raises(CursorError, match="unsigned"):
        decode(encode(_cursor()), SECRET)


def test_a_signed_cursor_is_refused_when_no_secret_is_configured() -> None:
    with pytest.raises(CursorError, match="no signing secret"):
        decode(encode(_cursor(), SECRET), None)


def test_another_users_cursor_is_refused() -> None:
    """The partitions named in a cursor are the ones its issuer could reach."""
    token = encode(_cursor(user_id="user-u"), SECRET)
    with pytest.raises(CursorError, match="different user"):
        decode(token, SECRET, expected_user_id="user-v")


def test_another_orgs_cursor_is_refused() -> None:
    """The org binding is checked, not inferred."""
    token = encode(_cursor(org_id="org-1"), SECRET)
    with pytest.raises(CursorError, match="different organization"):
        decode(token, SECRET, expected_org_id="org-2")


def test_the_issuers_own_cursor_is_accepted() -> None:
    """The control: the binding checks must not reject the legitimate case."""
    token = encode(_cursor(user_id="user-u", org_id="org-1"), SECRET)
    restored = decode(token, SECRET, expected_user_id="user-u", expected_org_id="org-1")
    assert restored.items_seen == 20


def test_expiry_is_only_checked_when_asked_for() -> None:
    stale = _cursor(issued_at_ms=int(time.time() * 1000) - 3_600_000)
    token = encode(stale, SECRET)
    assert decode(token, SECRET).items_seen == 20
    with pytest.raises(CursorError, match="expired"):
        decode(token, SECRET, max_age_seconds=60)


def test_a_negative_items_seen_is_refused() -> None:
    token = _tamper(encode(_cursor()), lambda w: w.__setitem__("sn", -5))
    with pytest.raises(CursorError, match="items seen"):
        decode(token)


def test_the_cursor_key_is_derived_not_the_shared_secret() -> None:
    """A cursor must never be signed with the key that signs JWTs and URLs."""
    derived = derive_cursor_secret("scoped-jwt-secret")
    assert derived == derive_cursor_secret(b"scoped-jwt-secret"), "must be deterministic"
    assert derived != b"scoped-jwt-secret"
    assert derived != derive_cursor_secret("another-secret")
    token = encode(_cursor(), derived)
    assert decode(token, derived).items_seen == 20
    with pytest.raises(CursorError, match="signature"):
        decode(token, "scoped-jwt-secret")


def test_a_missing_shared_secret_is_a_server_fault_not_a_bad_cursor() -> None:
    with pytest.raises(ValueError) as caught:
        derive_cursor_secret("")
    assert not isinstance(caught.value, CursorError)
