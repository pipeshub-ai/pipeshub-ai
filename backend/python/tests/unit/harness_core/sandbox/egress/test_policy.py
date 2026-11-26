"""EP-1..EP-11: the egress proxy's decisions as pure functions (P5.4 §1.2)."""

from __future__ import annotations

import pytest

from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused, status_for
from harness_core.sandbox.egress.policy import (
    REQUEST_ID_HEADER,
    ReflectionScrubber,
    check_credentials,
    check_destination,
    check_request_content,
    is_public_address,
    outbound_headers,
    parse_networks,
    parse_target,
    parse_tunnel_target,
    policy_errors,
    response_headers,
)
from harness_core.sandbox.egress.types import EgressBinding, ProxyRequest, Target
from harness_core.sandbox.types import (
    EgressDefault,
    EgressDestination,
    EgressPolicy,
    TokenRoute,
)

PH = "ph-model-0123456789abcdef"
PH_TOOLS = "ph-tools-0123456789abcdef"
POLICY = EgressPolicy(
    destinations=(
        EgressDestination("model-gateway", (8097,)),
        EgressDestination("tool-gateway", (8097,)),
        EgressDestination("docs.example.org", (80,)),
    ),
    token_routes=(
        TokenRoute(PH, "model-gateway", "gateway:model"),
        TokenRoute(PH_TOOLS, "tool-gateway", "gateway:tools"),
    ),
)
BINDING = EgressBinding("sbx-a", "10.213.0.2", "10.213.0.3", POLICY, {"runId": "r1"})


def req(target: str = "/v1", *, method: str = "POST", headers: dict[str, str] | None = None, body: bytes = b"") -> ProxyRequest:
    return ProxyRequest(method, target, tuple(({"Host": "model-gateway:8097"} if headers is None else headers).items()), body)


def refused(fn: object, *args: object, **kwargs: object) -> R:
    with pytest.raises(EgressRefused) as caught:
        fn(*args, **kwargs)  # type: ignore[operator]
    return caught.value.reason


class TestEP1Targets:
    @pytest.mark.parametrize(
        ("request_", "expected"),
        [
            (req("http://Model-Gateway.:8097/v1/chat?x=1"), Target("model-gateway", 8097, "/v1/chat?x=1")),
            (req("http://docs.example.org/a"), Target("docs.example.org", 80, "/a")),
            (req("/v1/x", headers={"Host": "tool-gateway:8097"}), Target("tool-gateway", 8097, "/v1/x")),
            (req("http://[2001:db8::1]:8080"), Target("2001:db8::1", 8080, "/")),
        ],
    )
    def test_parsed(self, request_: ProxyRequest, expected: Target) -> None:
        assert parse_target(request_) == expected

    @pytest.mark.parametrize(
        ("request_", "reason"),
        [
            (req("https://model-gateway/v1"), R.SCHEME_NOT_ALLOWED),
            (req("ftp://model-gateway/v1"), R.SCHEME_NOT_ALLOWED),
            (req("example.com:443", method="CONNECT"), R.TUNNEL_REFUSED),
            (req("http://user:pw@model-gateway/v1"), R.MALFORMED_REQUEST),
            (req("http:///v1"), R.MALFORMED_REQUEST),
            (req("http://model-gateway:99999/"), R.MALFORMED_REQUEST),
            (req("/v1", headers={}), R.MALFORMED_REQUEST),
            (req("/v1", headers={"Host": "a b"}), R.MALFORMED_REQUEST),
            (req("*", method="OPTIONS"), R.SCHEME_NOT_ALLOWED),
        ],
    )
    def test_refused(self, request_: ProxyRequest, reason: R) -> None:
        assert refused(parse_target, request_) == reason


class TestEP2Destinations:
    @pytest.mark.parametrize("target", [Target("model-gateway", 8097, "/"), Target("docs.example.org", 80, "/")])
    def test_allowlisted(self, target: Target) -> None:
        assert check_destination(target, POLICY).host == target.host

    @pytest.mark.parametrize(
        "target",
        [Target("model-gateway", 80, "/"), Target("evil.example", 8097, "/"), Target("model-gateway.evil", 8097, "/")],
    )
    def test_not_allowlisted(self, target: Target) -> None:
        assert refused(check_destination, target, POLICY) == R.DESTINATION_NOT_ALLOWED


# SEC-EG-06 (C6.08): host-name parser tricks. Each must be refused where the host is read, before any allowlist,
# resolver or upstream sees it; a later layer that maps the name (IDNA, a C resolver stopping at NUL, inet_aton's
# shorthand) would otherwise go somewhere other than where the proxy checked.
TRICK_HOSTS = [
    pytest.param("evil.com\x00.model-gateway", id="nul"),
    pytest.param("model-gateway\x00", id="trailing-nul"),
    pytest.param("evil.com%00.model-gateway", id="pct-nul"),
    pytest.param("model-gateway%2eevil.com", id="pct-dot"),
    pytest.param("\u0430llowed.example", id="cyrillic-a"),
    pytest.param("model\uff0egateway", id="fullwidth-dot"),
    pytest.param("\u212aey.example", id="kelvin-sign"),
    pytest.param("docs.example.org\u200b", id="zero-width"),
    pytest.param("b\u00fccher.example", id="idn-unicode"),
    pytest.param("a..b", id="empty-label"),
    pytest.param("127.1", id="ipv4-shorthand"),
    pytest.param("2130706433", id="ipv4-integer"),
    pytest.param("0x7f.1", id="ipv4-hex"),
    pytest.param("10.0.0.0x1", id="ipv4-hex-tail"),
    pytest.param("x" * 64 + ".example", id="long-label"),
]


class TestSecEg06HostTricks:
    @pytest.mark.parametrize("host", TRICK_HOSTS)
    def test_absolute_form_is_refused(self, host: str) -> None:
        assert refused(parse_target, req(f"http://{host}:8097/v1")) == R.MALFORMED_REQUEST

    @pytest.mark.parametrize("host", TRICK_HOSTS)
    def test_origin_form_host_header_is_refused(self, host: str) -> None:
        assert refused(parse_target, req("/v1", headers={"Host": f"{host}:8097"})) == R.MALFORMED_REQUEST

    @pytest.mark.parametrize("host", TRICK_HOSTS)
    def test_a_tunnel_target_is_refused(self, host: str) -> None:
        assert refused(parse_tunnel_target, req(f"{host}:443", method="CONNECT")) == R.MALFORMED_REQUEST

    @pytest.mark.parametrize("host", TRICK_HOSTS)
    def test_an_allowlist_entry_is_refused(self, host: str) -> None:
        errors = policy_errors(EgressPolicy(destinations=(EgressDestination(host, (80,)),)))
        assert any("malformed" in e for e in errors), errors

    @pytest.mark.parametrize(
        ("target", "reason"),
        [
            # The suffix and userinfo forms of the same trick, and the ASCII (punycode) form of a homoglyph: a real,
            # different name, so it is parsed and then not allowlisted.
            ("http://model-gateway.evil.example:8097/v1", R.DESTINATION_NOT_ALLOWED),
            ("http://model-gateway@evil.example:8097/v1", R.MALFORMED_REQUEST),
            ("http://evil.example@model-gateway:8097/v1", R.MALFORMED_REQUEST),
            ("http://xn--llowed-2nf.example/v1", R.DESTINATION_NOT_ALLOWED),
            ("http://MODEL-GATEWAY.:8097/v1", None),
        ],
    )
    def test_the_ascii_forms_go_through_the_allowlist(self, target: str, reason: R | None) -> None:
        try:
            parsed = parse_target(req(target))
        except EgressRefused as caught:
            assert caught.reason == reason
            return
        if reason is None:
            assert check_destination(parsed, POLICY).host == "model-gateway"
        else:
            assert refused(check_destination, parsed, POLICY) == reason

    @pytest.mark.parametrize(
        "host", ["model-gateway", "pxh_mongo-rs", "docs.example.org.", "10.1.2.3", "[2001:db8::1]", "xn--bcher-kva.example"]
    )
    def test_names_and_addresses_still_parse(self, host: str) -> None:
        assert parse_target(req(f"http://{host}/")).port == 80


VENDOR = [
    "sk-ant-api03-abcdefghijklmnopqrstuv",
    "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
    "sk-abcdefghijklmnopqrstuvwxyz0123",
    "ghp_" + "a" * 36,
    "github_pat_" + "a" * 30,
    "xoxb-1234567890-abcdefghij",
    "AKIAABCDEFGHIJKLMNOP",
    "AIza" + "b" * 35,
]


class TestEP3ForbiddenPatterns:
    @pytest.mark.parametrize("secret", VENDOR)
    @pytest.mark.parametrize("where", ["header", "url", "body"])
    def test_vendor_tokens_are_refused_anywhere(self, secret: str, where: str) -> None:
        request_ = {
            "header": req(headers={"Host": "model-gateway:8097", "X-Api-Key": secret}),
            "url": req(f"http://model-gateway:8097/v1?key={secret}"),
            "body": req(body=b'{"args": {"token": "' + secret.encode() + b'"}}'),
        }[where]
        assert refused(check_request_content, request_, POLICY, ()) == R.FORBIDDEN_PATTERN

    @pytest.mark.parametrize(
        "benign",
        ["task-abcdefghijklmnopqrstuvwxyz", "desk-0123456789012345678901", "risk-assessment-for-q3-2026-plan", "sk-short"],
    )
    def test_look_alikes_pass(self, benign: str) -> None:
        check_request_content(req(body=benign.encode()), POLICY, ())

    def test_a_policy_pattern_is_added(self) -> None:
        policy = EgressPolicy(destinations=POLICY.destinations, forbidden_patterns=(r"ACME-[0-9]{6}",))
        assert refused(check_request_content, req(body=b"id ACME-123456"), policy, ()) == R.FORBIDDEN_PATTERN

    def test_an_encoded_body_cannot_be_inspected(self) -> None:
        request_ = req(headers={"Host": "model-gateway:8097", "Content-Encoding": "gzip"}, body=b"\x1f\x8b")
        assert refused(check_request_content, request_, POLICY, ()) == R.BODY_ENCODED


class TestEP4Replay:
    @pytest.mark.parametrize(
        "request_",
        [
            req(body=b'{"x": "pxt_live-token-value"}'),
            req("http://model-gateway:8097/?t=pxt_live-token-value"),
            req(headers={"Host": "model-gateway:8097", "X-T": "pxt_live-token-value"}),
        ],
    )
    def test_a_live_token_anywhere_is_refused(self, request_: ProxyRequest) -> None:
        assert refused(check_request_content, request_, POLICY, {"pxt_live-token-value"}) == R.TOKEN_REPLAYED


def _foreign(value: str) -> bool:
    return value == "ph-other-sandbox-0000000000"


MODEL = Target("model-gateway", 8097, "/v1")
DOCS = Target("docs.example.org", 80, "/")


class TestEP5Credentials:
    def test_own_placeholder_to_its_destination(self) -> None:
        route = check_credentials(req(headers={"Authorization": f"Bearer {PH}"}), MODEL, BINDING, _foreign)
        assert route is not None and route.credential_ref == "gateway:model"

    @pytest.mark.parametrize(
        ("headers", "target", "reason"),
        [
            ({}, MODEL, R.PLACEHOLDER_MISSING),
            ({"Authorization": "Bearer guessed-value-123"}, MODEL, R.CALLER_AUTHORIZATION),
            ({"Authorization": f"Basic {PH}"}, MODEL, R.CALLER_AUTHORIZATION),
            ({"Authorization": "Bearer ph-other-sandbox-0000000000"}, MODEL, R.PLACEHOLDER_FOREIGN),
            ({"Authorization": f"Bearer {PH_TOOLS}"}, MODEL, R.PLACEHOLDER_WRONG_DESTINATION),
            ({"Authorization": f"Bearer {PH}"}, DOCS, R.PLACEHOLDER_WRONG_DESTINATION),
            ({"Authorization": "Bearer ph-other-sandbox-0000000000"}, DOCS, R.PLACEHOLDER_FOREIGN),
        ],
    )
    def test_refusals(self, headers: dict[str, str], target: Target, reason: R) -> None:
        assert refused(check_credentials, req(headers=headers), target, BINDING, _foreign) == reason

    def test_two_authorization_headers_are_refused(self) -> None:
        request_ = ProxyRequest("POST", "/v1", (("Authorization", f"Bearer {PH}"), ("Authorization", f"Bearer {PH}")))
        assert refused(check_credentials, request_, MODEL, BINDING, _foreign) == R.CALLER_AUTHORIZATION

    def test_caller_auth_to_an_uncredentialed_host_passes(self) -> None:
        assert check_credentials(req(headers={"Authorization": "Bearer theirs"}), DOCS, BINDING, _foreign) is None


class TestEP6Addresses:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.1.2.3",
            "172.17.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "100.64.0.1",
            "224.0.0.1",
            "0.0.0.0",
            "::1",
            "fd00::1",
            "fe80::1",
            "::ffff:10.0.0.1",
            "::ffff:169.254.169.254",
            "not-an-ip",
        ],
    )
    def test_non_public_is_refused(self, address: str) -> None:
        assert not is_public_address(address)

    def test_a_configured_cluster_range_is_refused(self) -> None:
        assert is_public_address("8.8.8.8")
        assert not is_public_address("8.8.8.8", parse_networks(["8.8.8.0/24"]))

    @pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
    def test_public_passes(self, address: str) -> None:
        assert is_public_address(address)


class TestEP7Headers:
    def test_outbound(self) -> None:
        request_ = req(
            headers={
                "Host": "model-gateway:8097",
                "Authorization": f"Bearer {PH}",
                "Proxy-Authorization": "Basic x",
                "Connection": "keep-alive, X-Drop",
                "X-Drop": "1",
                "Keep-Alive": "5",
                "Accept-Encoding": "gzip",
                "Content-Type": "application/json",
                REQUEST_ID_HEADER: "spoofed",
            }
        )
        out = dict(outbound_headers(request_, MODEL, token="pxt_tok", request_id="egr-1"))
        assert out == {
            "Content-Type": "application/json",
            "Host": "model-gateway:8097",
            "Accept-Encoding": "identity",
            REQUEST_ID_HEADER: "egr-1",
            "Authorization": "Bearer pxt_tok",
        }

    def test_no_token_means_no_authorization_added_and_caller_auth_dropped_only_for_routes(self) -> None:
        out = dict(outbound_headers(req(headers={"Host": "docs.example.org"}), DOCS, token=None, request_id="e"))
        assert "Authorization" not in out and out["Host"] == "docs.example.org"

    def test_response_headers_are_scrubbed(self) -> None:
        out = response_headers(
            [("Content-Length", "10"), ("Transfer-Encoding", "chunked"), ("X-Echo", "Bearer pxt_tok")], [("pxt_tok", PH)]
        )
        assert out == (("X-Echo", f"Bearer {PH}"),)


class TestEP8Scrubber:
    @pytest.mark.parametrize(
        "chunks",
        [
            [b"a pxt_secret b"],
            [b"a pxt_se", b"cret b"],
            [b"a p", b"xt_secr", b"et b"],
            [b"pxt_secretpxt_secret"],
            [b"no secret here"],
            [b"ends with pxt_sec"],
        ],
    )
    def test_reassembled_output_never_has_the_secret(self, chunks: list[bytes]) -> None:
        scrubber = ReflectionScrubber("pxt_secret", "PH")
        out = b"".join(scrubber.feed(c) for c in chunks) + scrubber.finish()
        assert out == b"".join(chunks).replace(b"pxt_secret", b"PH")

    def test_event_tails_are_not_held_back(self) -> None:
        scrubber = ReflectionScrubber("pxt_secret", "PH")
        assert scrubber.feed(b"data: {}\n\n") == b"data: {}\n\n"


class TestEP9PolicyValidation:
    def test_a_good_policy_has_no_errors(self) -> None:
        assert policy_errors(POLICY) == []
        assert policy_errors(EgressPolicy()) == []
        assert policy_errors(EgressPolicy(destinations=(EgressDestination("a", upstream="http://worker:8090/gw/k1"),))) == []

    @pytest.mark.parametrize(
        ("policy", "fragment"),
        [
            (EgressPolicy(destinations=(EgressDestination("a"),), token_routes=(TokenRoute("short", "a", "r"),)), "shorter"),
            (
                EgressPolicy(destinations=(EgressDestination("a"),), token_routes=(TokenRoute("sk-" + "a" * 30, "a", "r"),)),
                "vendor credential",
            ),
            (
                EgressPolicy(
                    destinations=(EgressDestination("a"), EgressDestination("b")),
                    token_routes=(TokenRoute(PH, "a", "r"), TokenRoute(PH, "b", "r")),
                ),
                "reuses a placeholder",
            ),
            (
                EgressPolicy(
                    destinations=(EgressDestination("a"),),
                    token_routes=(TokenRoute(PH, "a", "r"), TokenRoute(PH_TOOLS, "a", "r")),
                ),
                "second route",
            ),
            (EgressPolicy(destinations=(EgressDestination("a"),), token_routes=(TokenRoute(PH, "b", "r"),)), "not allowlisted"),
            (EgressPolicy(destinations=(EgressDestination("a"),), forbidden_patterns=("([",)), "does not compile"),
            *(
                (EgressPolicy(destinations=(EgressDestination("a", upstream=bad),)), "upstream")
                for bad in (
                    "ftp://worker:1/x",
                    "http://user:pw@worker:1/x",
                    "http://worker:1/x?k=1",
                    "http://worker:1/x#f",
                    "worker:1/x",
                    "http://worker:99999/x",
                )
            ),
            (EgressPolicy(default=EgressDefault.ALLOW, destinations=(EgressDestination("a"),)), "unrestricted"),
            (EgressPolicy(destinations=(EgressDestination("a", (0,)),)), "port"),
            (EgressPolicy(destinations=(EgressDestination("a/b"),)), "malformed"),
        ],
    )
    def test_each_problem_is_named(self, policy: EgressPolicy, fragment: str) -> None:
        errors = policy_errors(policy)
        assert len(errors) == 1 and fragment in errors[0], errors


class TestEP10SubnetPool:
    def test_allocation(self) -> None:
        pool = SubnetPool("10.213.0.0/28")
        first = pool.allocate(())
        assert first is not None and (first.cidr, first.proxy_address, first.sandbox_address) == (
            "10.213.0.0/29",
            "10.213.0.2",
            "10.213.0.3",
        )
        second = pool.allocate({first.cidr})
        assert second is not None and second.cidr == "10.213.0.8/29"
        assert pool.allocate({first.cidr, second.cidr}) is None
        assert pool.contains("10.213.0.8/29") and not pool.contains("10.214.0.0/29")

    @pytest.mark.parametrize("cidr", ["10.213.0.0/30", "fd00::/64"])
    def test_unusable_pools_are_refused(self, cidr: str) -> None:
        with pytest.raises(ValueError, match="pool"):
            _ = SubnetPool(cidr)


def test_ep11_reasons_are_unique_and_have_a_status() -> None:
    values = [r.value for r in R]
    assert len(values) == len(set(values))
    assert all(400 <= status_for(r) < 600 for r in R)
