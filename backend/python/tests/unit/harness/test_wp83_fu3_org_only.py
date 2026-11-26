"""WP83-FU-3: no outside probe; the admin declares what only the organization reaches.

One classifier (``harness_core.security.reachability.PublicReachability``) answers for ``fetch_url`` and
``repository_fetch``: a host or range in ``websites.orgOnlyAddresses`` is never public, and a git host an admin added
by hand is not public-readable until the admin says so (``publicReadable``); a preset's host is. Unclaimed stays
unknown, so after such a read a send outside the organization asks (the flow card), as after any private read.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.labels import call_provenance
from app.harness.repositories import org_reachability
from app.harness.web_reachability import ORG_ONLY_PAGE, PageReview, page_url
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.policy.policy_layer import PolicyLayer
from harness_core.effects.types import ToolExecution
from harness_core.provenance.rules import WEB_RULE
from harness_core.provenance.stamp import PROVENANCE_EVENT, ProvenanceStamp
from harness_core.repos.fetch import FetchConfig
from harness_core.sandbox.egress.policy import parse_networks
from harness_core.security.reachability import (
    UNREADABLE,
    PublicReachability,
    org_only_entries,
    publicly_readable,
)
from tests.unit.harness.test_gitf_repository_fetch import (  # noqa: F401 - the fixtures the tool tests share
    CTX,
    HOST,
    _call,
    _Resolver,
    _Shelf,
    _tools,
    https_host,
    roomy_disk,
)
from tests.unit.harness.test_wp83_public_clones import _PublicLoopback

if TYPE_CHECKING:
    from tests.support.git_host import GitHttpHost

ORG_ONLY = ["intranet.acme.com", "*.corp.acme.com", "203.0.113.0/24", "2001:db8::/32", "198.51.100.7"]


def _page(url: str) -> ToolExecution:
    return ToolExecution("ok", json.dumps({"ok": True, "result_type": "url_content", "url": url, "blocks": []}))


def _lookup(answers: dict[str, list[str]], asked: list[str]) -> Any:  # noqa: ANN401
    async def resolve(host: str) -> list[str]:
        asked.append(host)
        if host not in answers:
            raise OSError("no such host")
        return answers[host]

    return resolve


def _of(entries: list[str]) -> Any:  # noqa: ANN401
    async def reachability() -> PublicReachability:
        return PublicReachability.of(entries)

    return reachability


# --- the classifier ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "addresses", "org_only"),
    [
        ("intranet.acme.com", ["140.82.112.3"], True),
        ("INTRANET.acme.com.", ["140.82.112.3"], True),
        ("wiki.corp.acme.com", ["140.82.112.3"], True),
        ("corp.acme.com", ["140.82.112.3"], False),
        ("git.acme.com", ["203.0.113.9"], True),
        ("git.acme.com", ["2001:db8::9"], True),
        ("git.acme.com", ["::ffff:198.51.100.7"], True),
        ("git.acme.com", ["140.82.112.3", "203.0.113.9"], True),
        ("docs.python.org", ["151.101.0.223"], False),
        # Ranges are declared, so an address is needed to clear content: none known is not cleared.
        ("docs.python.org", [], True),
    ],
)
def test_wp83_fu3_1_names_wildcards_addresses_and_ranges(host: str, addresses: list[str], org_only: bool) -> None:
    assert PublicReachability.of(ORG_ONLY).org_only(host, addresses) is org_only


def test_wp83_fu3_2_an_org_that_declares_nothing_clears_without_a_lookup_and_unreadable_clears_nothing() -> None:
    nothing = PublicReachability.of([])
    assert nothing.org_only("docs.python.org", []) is False and not nothing.declares_ranges
    names_only = PublicReachability.of(["intranet.acme.com"])
    assert names_only.org_only("docs.python.org", []) is False and not names_only.declares_ranges
    assert UNREADABLE.org_only("docs.python.org", ["151.101.0.223"]) is True
    # A form this worker cannot read is skipped, never widening what is cleared; a bad item is not a crash.
    assert PublicReachability.of(["", 7, "*", "10.0.0.0/33"]).names == frozenset()
    refused = PublicReachability.of([], parse_networks(["100.64.0.0/10"]))
    assert refused.address_public("140.82.112.3") and not refused.address_public("100.64.1.1")


def test_wp83_fu3_3_a_preset_host_is_readable_a_hand_added_one_only_when_the_admin_says() -> None:
    assert publicly_readable({"host": "github.com", "preset": "github-public"}) is True
    assert publicly_readable({"host": "git.example.com"}) is False
    assert publicly_readable({"host": "git.example.com", "publicReadable": False}) is False
    assert publicly_readable({"host": "git.example.com", "publicReadable": True}) is True
    assert publicly_readable(None) is False


def test_wp83_fu3_4_the_live_layer_s_org_list_is_the_only_one_read_and_the_contract_holds_it() -> None:
    websites: dict[str, Any] = {
        "org": {"sites": [], "orgOnlyAddresses": ORG_ONLY},
        "agents": {"a1": {"sites": [], "orgOnlyAddresses": ["ignored.example"]}},
    }
    assert org_only_entries(websites) == ORG_ONLY
    assert org_only_entries(None) == [] and org_only_entries({"org": {"sites": []}}) == []
    layer = {
        "schemaVersion": 1,
        "scope": {"kind": "org", "id": "org-1"},
        "websites": {"sites": [], "orgOnlyAddresses": ORG_ONLY},
        "repositories": {"hosts": [{"host": "git.example.com", "publicReadable": True}]},
    }
    _ = validate_contract(PolicyLayer, layer)
    with pytest.raises(Exception, match="orgOnlyAddresses|pattern|String should match"):
        _ = validate_contract(PolicyLayer, {**layer, "websites": {"sites": [], "orgOnlyAddresses": ["NOT A HOST!"]}})


async def test_wp83_fu3_5_a_run_reads_its_policy_s_list_at_each_call() -> None:
    held: list[object] = ["intranet.acme.com"]

    class Policy:
        async def org_only_addresses(self) -> list[object]:
            return list(held)

    reach = org_reachability(cast(Any, Policy()))
    assert (await reach()).name_org_only("intranet.acme.com")
    held.clear()
    assert not (await reach()).name_org_only("intranet.acme.com")


def test_r27_seam_a_policy_without_the_member_fails_the_build_not_the_check() -> None:
    """An edition's policy wrapper that forgot ``org_only_addresses`` once read as "no org-only address", so every
    page of an org-only host counted as public. It now fails where the run is built."""

    class Wrapper:
        async def tool_disclosure(self) -> None: ...

    with pytest.raises(AttributeError, match="org_only_addresses"):
        _ = org_reachability(cast(Any, Wrapper()))


# --- fetch_url ------------------------------------------------------------------------------------------------------


async def test_wp83_fu3_6_an_org_only_page_is_claimed_unknown_and_a_public_one_is_untouched() -> None:
    asked: list[str] = []
    review = PageReview(_of(ORG_ONLY), _lookup({"docs.python.org": ["151.101.0.223"], "wiki.acme.com": ["203.0.113.4"]}, asked))
    assert (await review("dynamic__fetch_url", _page("https://intranet.acme.com/plan"))).provenance == ORG_ONLY_PAGE
    assert (await review("fetch_url", _page("https://wiki.acme.com/x"))).provenance == ORG_ONLY_PAGE
    public = _page("https://docs.python.org/3/")
    assert (await review("dynamic__fetch_url", public)) is public
    # A host that does not resolve cannot be checked against the ranges: not cleared.
    assert (await review("dynamic__fetch_url", _page("https://gone.example/"))).provenance == ORG_ONLY_PAGE
    # A failed fetch and another tool are left alone.
    failed = ToolExecution("ok", json.dumps({"ok": False, "error_type": "blocked"}))
    assert (await review("dynamic__fetch_url", failed)) is failed
    other = _page("https://intranet.acme.com/")
    assert (await review("dynamic__web_search", other)) is other
    assert asked == ["wiki.acme.com", "docs.python.org", "gone.example"]


async def test_wp83_fu3_7_no_lookup_when_the_org_lists_no_range_and_an_unreadable_list_clears_nothing() -> None:
    asked: list[str] = []
    review = PageReview(_of(["intranet.acme.com"]), _lookup({}, asked))
    public = _page("https://docs.python.org/3/")
    assert (await review("dynamic__fetch_url", public)) is public and asked == []

    async def broken() -> PublicReachability:
        raise RuntimeError("live layer down")

    assert (await PageReview(broken, _lookup({}, asked))("dynamic__fetch_url", public)).provenance == ORG_ONLY_PAGE
    assert page_url('prefix {"url": "https://a.example/"}') == "https://a.example/"
    assert page_url({"ok": True, "url": "https://b.example/"}) == "https://b.example/"


async def test_wp83_fu3_8_the_stamp_records_the_page_unknown_so_the_flow_card_shows() -> None:
    """WP83-FLOW: the stamp's review runs before the rule decides; the page's citation delta stays named."""
    stamp = ProvenanceStamp(
        {"dynamic__fetch_url": WEB_RULE}, journal=True, sources_of=call_provenance,
        review=PageReview(_of(ORG_ONLY), _lookup({"docs.python.org": ["151.101.0.223"]}, [])),
    )
    reviewed = await stamp.reviewed("dynamic__fetch_url", _page("https://intranet.acme.com/plan"))
    recorded = stamp.stamp("dynamic__fetch_url", "web@default/fetch_url", "inv-1", reviewed)
    record = next(e for e in recorded.events if e.type == PROVENANCE_EVENT)
    assert record.data["provenance"]["readers"] == "unknown"  # type: ignore[index]
    public = await stamp.reviewed("dynamic__fetch_url", _page("https://docs.python.org/3/"))
    assert stamp.provenance("dynamic__fetch_url", public).readers == "public"


# --- repository_fetch -----------------------------------------------------------------------------------------------


async def test_wp83_fu3_9_a_hand_added_host_defaults_to_unknown_a_preset_stays_public_an_org_only_one_is_unknown(
    https_host: tuple[GitHttpHost, Path, str, int],  # noqa: F811 - the shared fixture
    tmp_path: Path,
) -> None:
    _, _, ca, port = https_host
    config = FetchConfig(cache_dir=tmp_path / "cache", git_bin="git", port=port, ca_file=ca)
    call = _call(f"https://{HOST}/acme/widgets")

    async def run(hosts: list[dict[str, Any]], entries: list[str]) -> ToolExecution:
        return await _tools(_PublicLoopback(config, _Resolver({})), _Shelf(), hosts=hosts, reachability=_of(entries)).execute(
            call, CTX
        )

    hand_added = await run([{"host": HOST}], [])
    assert (hand_added.status, hand_added.public) == ("ok", False), hand_added.content
    assert (await run([{"host": HOST, "preset": "github-public"}], [])).public is True
    assert (await run([{"host": HOST, "publicReadable": True}], [])).public is True
    # Declared org-only by name, or by the range git dialled (the test host's loopback): unknown, cached or not.
    assert (await run([{"host": HOST, "publicReadable": True}], [HOST])).public is False
    assert (await run([{"host": HOST, "publicReadable": True}], ["127.0.0.0/8"])).public is False
    assert (await run([{"host": HOST, "publicReadable": True}], ["203.0.113.0/24"])).public is True


async def test_wp83_fu3_10_an_old_cache_entry_without_its_address_is_unknown_while_the_org_declares_ranges(
    https_host: tuple[GitHttpHost, Path, str, int],  # noqa: F811 - the shared fixture
    tmp_path: Path,
) -> None:
    _, _, ca, port = https_host
    config = FetchConfig(cache_dir=tmp_path / "cache", git_bin="git", port=port, ca_file=ca)
    call = _call(f"https://{HOST}/acme/widgets")
    readable = [{"host": HOST, "publicReadable": True}]
    first = await _tools(_PublicLoopback(config, _Resolver({})), _Shelf(), hosts=readable).execute(call, CTX)
    assert first.public is True
    for meta in (tmp_path / "cache").rglob("meta.json"):
        old = json.loads(meta.read_text())
        assert old["address"] == "127.0.0.1"
        del old["address"]
        meta.write_text(json.dumps(old))
    again = _tools(_PublicLoopback(config, _Resolver({})), _Shelf(), hosts=readable, reachability=_of(["203.0.113.0/24"]))
    assert (await again.execute(call, CTX)).public is False
    names_only = _tools(_PublicLoopback(config, _Resolver({})), _Shelf(), hosts=readable, reachability=_of(["x.example"]))
    assert (await names_only.execute(call, CTX)).public is True


async def test_wp83_fu3_11_cross_tenant_one_org_s_list_never_clears_or_holds_another_s(
    https_host: tuple[GitHttpHost, Path, str, int],  # noqa: F811 - the shared fixture
    tmp_path: Path,
) -> None:
    """Each run reads its own org's list: org A's declaration holds A's clone and leaves B's, from the same cache."""
    _, _, ca, port = https_host
    config = FetchConfig(cache_dir=tmp_path / "cache", git_bin="git", port=port, ca_file=ca)
    fetcher = _PublicLoopback(config, _Resolver({}))
    call = _call(f"https://{HOST}/acme/widgets")
    readable = [{"host": HOST, "publicReadable": True}]
    a = await _tools(fetcher, _Shelf(), hosts=readable, reachability=_of([HOST])).execute(call, CTX)
    b = await _tools(fetcher, _Shelf(), hosts=readable, reachability=_of([])).execute(
        call, dataclasses.replace(CTX, org_id="org-2")
    )
    assert (a.public, b.public) == (False, True)


# --- the vector both sides replay -------------------------------------------------------------------------------------

VECTOR = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts/harness/vectors/org-only-addresses.json").read_text()
)


def _schema_ok(entry: str) -> bool:
    layer = {"schemaVersion": 1, "scope": {"kind": "org", "id": "org-1"}, "websites": {"sites": [], "orgOnlyAddresses": [entry]}}
    try:
        _ = validate_contract(PolicyLayer, layer)
    except ValueError:
        return False
    return True


def _reads(entry: str) -> bool:
    reach = PublicReachability.of([entry])
    return len(reach.networks) + len(reach.names) == 1


def test_wp83_fu3_12_the_vector_s_entries_and_cases() -> None:
    for entry in VECTOR["valid"]:
        assert _schema_ok(entry) and _reads(entry), entry
    for entry in VECTOR["invalid"]:
        assert not (_schema_ok(entry) and _reads(entry)), entry
    for case in VECTOR["cases"]:
        assert PublicReachability.of(case["entries"]).org_only(case["host"], case["addresses"]) is case["orgOnly"], case["name"]
    for case in VECTOR["readable"]:
        assert publicly_readable(case["entry"]) is case["readable"], case["name"]
