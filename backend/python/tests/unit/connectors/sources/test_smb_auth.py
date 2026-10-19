from __future__ import annotations

import pytest

from app.connectors.sources.smb.auth import (
    auth_problems,
    is_server_name,
    is_share_name,
    parse_port,
    port_of,
)

VALID = {
    "server": "fileserver.corp.example",
    "port": "445",
    "domain": "CORP",
    "username": "svc-pipeshub",
    "password": "secret",
    "share": "departments",
}


class TestParsePort:
    @pytest.mark.parametrize(("value", "port"), [("445", 445), (445, 445), (" 139 ", 139), ("65535", 65535), (1, 1)])
    def test_ports(self, value: object, port: int) -> None:
        assert parse_port(value) == port

    @pytest.mark.parametrize("value", ["0", "65536", "99999", "abc", "-1", "4.5", "", None, True, 4.0])
    def test_not_ports(self, value: object) -> None:
        assert parse_port(value) is None

    def test_a_missing_port_is_the_default(self) -> None:
        assert port_of({}) == 445


class TestServerName:
    @pytest.mark.parametrize(
        "server", ["fs01", "fs01.corp.example", "fs01.corp.example.", "10.0.0.5", "fd00::5", "[fd00::5]", "FILE_SRV"]
    )
    def test_hostnames_and_addresses(self, server: str) -> None:
        assert is_server_name(server)

    @pytest.mark.parametrize(
        "server", ["\\\\fs01", "fs01\\share", "fs01/share", "fs 01", "-fs01", "fs01..corp", "user@fs01", "a" * 254]
    )
    def test_anything_else(self, server: str) -> None:
        assert not is_server_name(server)


class TestShareName:
    @pytest.mark.parametrize("share", ["departments", "Data$", "Team Share", "a" * 80])
    def test_share_names(self, share: str) -> None:
        assert is_share_name(share)

    @pytest.mark.parametrize("share", ["..", ".", "a/b", "a\\b", "c:", "x*", "a" * 81, "tab\there"])
    def test_anything_else(self, share: str) -> None:
        assert not is_share_name(share)


class TestAuthProblems:
    def test_valid_settings_have_none(self) -> None:
        assert auth_problems(VALID) == []

    def test_domain_and_port_are_optional(self) -> None:
        assert auth_problems({**VALID, "domain": "", "port": ""}) == []

    @pytest.mark.parametrize(
        ("change", "problem"),
        [
            ({"server": ""}, "Server is required."),
            ({"server": "192.0.2.1\\evil"}, "Server must be"),
            ({"port": "99999"}, "Port must be"),
            ({"port": "abc"}, "Port must be"),
            ({"port": "\u00b2"}, "Port must be"),
            ({"username": "  "}, "Username is required."),
            ({"password": ""}, "Password is required."),
            ({"password": 12345}, "Password must be text."),
            ({"password": ["p"]}, "Password must be text."),
            ({"share": ""}, "Share is required."),
            ({"share": "qa/../other"}, "Share must be"),
            ({"share": ".."}, "Share must be"),
        ],
    )
    def test_each_bad_field_is_named(self, change: dict[str, str], problem: str) -> None:
        problems = auth_problems({**VALID, **change})

        assert len(problems) == 1
        assert problems[0].startswith(problem)

    def test_a_missing_password_is_a_problem(self) -> None:
        settings = {key: value for key, value in VALID.items() if key != "password"}

        assert auth_problems(settings) == ["Password is required."]
