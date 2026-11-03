"""Tests for the per-container egress firewall (``egress_firewall``)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding.egress_firewall import (
    BLOCKED_CIDRS,
    FIREWALL_UNAVAILABLE_EXIT_CODE,
    build_firewall_script,
    ensure_egress_network_sync,
    firewall_unavailable,
    firewalled_container_kwargs,
    new_firewall_token,
    parse_cidrs,
)

_ICC = "com.docker.network.bridge.enable_icc"
_TOKEN = "0123456789abcdef0123456789abcdef"


def _index(script: str, needle: str) -> int:
    lines = script.splitlines()
    return next(i for i, line in enumerate(lines) if needle in line)


class TestBuildFirewallScript:
    def test_rejects_every_non_public_range_metadata_and_the_bridge_subnet(self) -> None:
        script = build_firewall_script(["203.0.200.0/24"], token=_TOKEN)
        for cidr in (*BLOCKED_CIDRS, "169.254.0.0/16", "168.63.129.16/32", "203.0.200.0/24"):
            assert f"r -A OUTPUT -d {cidr} -j REJECT" in script

    def test_loopback_is_accepted_first_so_embedded_dns_works(self) -> None:
        script = build_firewall_script(["172.30.0.0/16"], token=_TOKEN)
        assert _index(script, "-o lo -j ACCEPT") < _index(script, "-j REJECT")

    def test_allowance_cannot_open_metadata_but_precedes_private_ranges(self) -> None:
        script = build_firewall_script([], allowed_cidrs=["0.0.0.0/0", "10.20.0.0/16"], token=_TOKEN)
        assert _index(script, "169.254.0.0/16 -j REJECT") < _index(script, "0.0.0.0/0 -j ACCEPT")
        assert _index(script, "10.20.0.0/16 -j ACCEPT") < _index(script, "10.0.0.0/8 -j REJECT")

    def test_drops_to_unprivileged_user_after_the_rules(self) -> None:
        script = build_firewall_script(["172.30.0.0/16"], run_as="runner", token=_TOKEN)
        last = script.strip().splitlines()[-1]
        assert last.startswith("exec setpriv --reuid=runner --regid=runner --init-groups")
        assert "--bounding-set=-all" in last and "--inh-caps=-all" in last and "--no-new-privs" in last
        assert last.endswith('-- "$@"')
        assert 'h="$(getent passwd runner | cut -d: -f6)"' in script

    def test_run_as_is_shell_quoted(self) -> None:
        assert "--reuid='a b;x'" in build_firewall_script([], run_as="a b;x", token=_TOKEN)


def test_container_kwargs_wrap_command_and_drop_everything_else() -> None:
    kwargs = firewalled_container_kwargs(["python3", "main.py"], network_cidrs=["172.30.0.0/16"], token=_TOKEN)
    assert kwargs["command"][:2] == ["sh", "-c"]
    assert kwargs["command"][4:] == ["python3", "main.py"]
    assert kwargs["user"] == "0"
    assert kwargs["cap_drop"] == ["ALL"]
    assert set(kwargs["cap_add"]) == {"NET_ADMIN", "SETUID", "SETGID", "SETPCAP"}
    assert "NET_RAW" not in kwargs["cap_add"]
    assert kwargs["security_opt"] == ["no-new-privileges:true"]


@pytest.mark.parametrize(
    ("exit_code", "stderr", "token", "expected"),
    [
        (FIREWALL_UNAVAILABLE_EXIT_CODE, f"[sandbox-egress] firewall unavailable ({_TOKEN}): no iptables", _TOKEN, True),
        (FIREWALL_UNAVAILABLE_EXIT_CODE, "user program chose this exit code", _TOKEN, False),
        (1, f"[sandbox-egress] firewall unavailable ({_TOKEN}): no iptables", _TOKEN, False),
        # A program can print the marker and exit 222, but can't know the token.
        (FIREWALL_UNAVAILABLE_EXIT_CODE, "[sandbox-egress] firewall unavailable: forged", _TOKEN, False),
        (FIREWALL_UNAVAILABLE_EXIT_CODE, f"[sandbox-egress] firewall unavailable ({'f' * 32}): forged", _TOKEN, False),
        (FIREWALL_UNAVAILABLE_EXIT_CODE, "[sandbox-egress] firewall unavailable (): x", "", False),
    ],
)
def test_firewall_unavailable_needs_code_marker_and_token(exit_code, stderr, token, expected) -> None:
    assert firewall_unavailable(exit_code, stderr, token) is expected


def test_failure_marker_in_script_carries_the_token() -> None:
    script = build_firewall_script(["172.30.0.0/16"], token=_TOKEN)
    assert f"[sandbox-egress] firewall unavailable ({_TOKEN}):" in script


def test_new_tokens_are_valid_and_unique() -> None:
    tokens = {new_firewall_token() for _ in range(50)}
    assert len(tokens) == 50
    for token in tokens:
        build_firewall_script([], token=token)


@pytest.mark.parametrize("token", ["", "short", "NOT-HEX-0123456789", "abc'; id; '0123456789abcdef"])
def test_script_refuses_a_malformed_token(token) -> None:
    with pytest.raises(ValueError):
        build_firewall_script([], token=token)


class TestCidrsAreValidatedBeforeTheyReachTheShell:
    @pytest.mark.parametrize(
        "bad",
        [
            "1.1.1.1/32 -j ACCEPT; id >&2; true",
            "10.0.0.0/8$(id)",
            "`id`",
            "not-a-cidr",
            "fd00::/8",
        ],
    )
    @pytest.mark.parametrize("where", ["allowed", "network"])
    def test_non_ipv4_cidr_is_refused(self, bad, where) -> None:
        kwargs = {"allowed_cidrs": [bad]} if where == "allowed" else {}
        network = [bad] if where == "network" else []
        with pytest.raises(ValueError):
            build_firewall_script(network, token=_TOKEN, **kwargs)

    def test_valid_cidrs_are_normalised(self) -> None:
        script = build_firewall_script(["172.30.0.1/16"], allowed_cidrs=["10.20.3.4/16"], token=_TOKEN)
        assert "-d 172.30.0.0/16 -j REJECT" in script
        assert "-d 10.20.0.0/16 -j ACCEPT" in script


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ()),
        ("", ()),
        ("10.1.2.3/16, 192.168.5.0/24", ("10.1.0.0/16", "192.168.5.0/24")),
        ("not-a-cidr,10.0.0.0/8", ("10.0.0.0/8",)),
        ("fd00::/8", ()),
    ],
)
def test_parse_cidrs(raw, expected) -> None:
    assert parse_cidrs(raw) == expected


def _network(name: str, *, icc_off: bool = True, containers: dict | None = None, subnet: str = "172.30.0.0/16") -> MagicMock:
    network = MagicMock()
    network.name = name
    network.id = "a" * 64
    network.attrs = {
        "Options": {_ICC: "false"} if icc_off else {},
        "IPAM": {"Config": [{"Subnet": subnet}, {"Subnet": "fd00::/64"}]},
        "Containers": containers or {},
    }
    return network


class TestEnsureEgressNetwork:
    def test_creates_with_icc_off_and_returns_ipv4_subnets(self) -> None:
        client = MagicMock()
        created = _network("egress")
        client.networks.list.side_effect = [[], [created]]
        client.networks.get.return_value = created

        assert ensure_egress_network_sync(client, "egress", {"l": "v"}) == ["172.30.0.0/16"]
        kwargs = client.networks.create.call_args.kwargs
        assert kwargs["options"] == {_ICC: "false"}
        assert kwargs["enable_ipv6"] is False

    def test_subnet_that_is_not_a_cidr_is_dropped(self) -> None:
        client = MagicMock()
        odd = _network("egress", subnet="172.30.0.0/16; id")
        client.networks.list.return_value = [odd]
        client.networks.get.return_value = odd

        assert ensure_egress_network_sync(client, "egress", {}) == []

    def test_idle_network_with_icc_on_is_recreated(self) -> None:
        client = MagicMock()
        legacy = _network("egress", icc_off=False)
        fresh = _network("egress", subnet="172.31.0.0/16")
        client.networks.list.side_effect = [[legacy], [fresh]]
        client.networks.get.side_effect = [legacy, fresh]

        assert ensure_egress_network_sync(client, "egress", {}) == ["172.31.0.0/16"]
        legacy.remove.assert_called_once()
        client.networks.create.assert_called_once()

    def test_in_use_network_with_icc_on_is_kept(self) -> None:
        client = MagicMock()
        legacy = _network("egress", icc_off=False, containers={"c": {}})
        client.networks.list.return_value = [legacy]
        client.networks.get.return_value = legacy

        assert ensure_egress_network_sync(client, "egress", {}) == ["172.30.0.0/16"]
        legacy.remove.assert_not_called()
        client.networks.create.assert_not_called()

    def test_substring_match_is_not_the_network(self) -> None:
        client = MagicMock()
        near_miss = _network("pipeshub_sandbox_egress")
        created = _network("sandbox_egress")
        client.networks.list.side_effect = [[near_miss], [near_miss, created]]
        client.networks.get.return_value = created

        ensure_egress_network_sync(client, "sandbox_egress", {})
        assert client.networks.create.call_args.kwargs["name"] == "sandbox_egress"

    def test_original_error_survives_a_failing_recheck(self) -> None:
        client = MagicMock()
        client.networks.list.side_effect = [[], RuntimeError("daemon gone")]
        client.networks.create.side_effect = RuntimeError("create failed")
        with pytest.raises(RuntimeError, match="create failed"):
            ensure_egress_network_sync(client, "egress", {})


class TestCreateSandboxContainerDiskQuota:
    """Per-run writable-layer quota (storage_opt) bounds host-disk use
    (CWE-400): applied where the daemon accepts the option, falls back with a
    warning where it rejects it, and never breaks a run."""

    def _reset(self) -> None:
        import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
        ef._storage_opt_usable = None

    def test_applies_quota_and_logs_once(self, monkeypatch, caplog) -> None:
        import logging

        import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
        self._reset()
        monkeypatch.setenv("SANDBOX_DISK_QUOTA", "7g")
        client = MagicMock()
        with caplog.at_level(logging.INFO):
            ef.create_sandbox_container(client, image="img", detach=True)
            ef.create_sandbox_container(client, image="img", detach=True)
        for call in client.containers.create.call_args_list:
            assert call.kwargs["storage_opt"] == {"size": "7g"}
        applied = [r for r in caplog.records if "Applying a per-run disk quota" in r.getMessage()]
        assert len(applied) == 1  # logged once, not per container

    def test_rejected_option_falls_back_and_warns_once(self, monkeypatch, caplog) -> None:
        import logging

        import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
        self._reset()
        monkeypatch.setenv("SANDBOX_DISK_QUOTA", "10g")
        client = MagicMock()
        ok = MagicMock()
        # 1st create (with storage_opt) rejected; retry (without) succeeds.
        client.containers.create.side_effect = [
            Exception("--storage-opt is supported only for overlay over xfs with 'pquota'"),
            ok, ok, ok,
        ]
        with caplog.at_level(logging.WARNING):
            assert ef.create_sandbox_container(client, image="img", detach=True) is ok
            # cached unusable: later creates skip storage_opt entirely.
            ef.create_sandbox_container(client, image="img", detach=True)
        first, second, third = client.containers.create.call_args_list
        assert "storage_opt" in first.kwargs
        assert "storage_opt" not in second.kwargs and "storage_opt" not in third.kwargs
        warns = [r for r in caplog.records if "was rejected by this Docker daemon" in r.getMessage()]
        assert len(warns) == 1

    def test_unrelated_create_error_is_not_swallowed(self, monkeypatch) -> None:
        import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
        self._reset()
        monkeypatch.setenv("SANDBOX_DISK_QUOTA", "10g")
        client = MagicMock()
        client.containers.create.side_effect = Exception("image not found")
        with pytest.raises(Exception, match="image not found"):
            ef.create_sandbox_container(client, image="img", detach=True)
        assert client.containers.create.call_count == 1  # no fallback retry

    def test_quota_disabled_sends_no_storage_opt(self, monkeypatch) -> None:
        import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
        self._reset()
        monkeypatch.setenv("SANDBOX_DISK_QUOTA", "0")
        client = MagicMock()
        ef.create_sandbox_container(client, image="img", detach=True)
        assert "storage_opt" not in client.containers.create.call_args.kwargs


class TestIsStorageOptRejected:
    def test_recognises_storage_opt_rejections(self) -> None:
        import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
        for m in ("--storage-opt is supported only for overlay over xfs with 'pquota'",
                  "Storage Opt is not supported", "invalid size option"):
            assert ef._is_storage_opt_rejected(Exception(m)) is True

    def test_other_errors_are_not_rejections(self) -> None:
        import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
        assert ef._is_storage_opt_rejected(Exception("No such image: img")) is False
