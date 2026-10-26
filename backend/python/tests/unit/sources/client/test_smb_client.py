import socket
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.connectors.sources.network_share.errors import (
    AccessDeniedError,
    DirectoryListingError,
    NetworkShareAuthError,
)
from app.sources.client.smb.smb import (
    CONNECTION_TIMEOUT_SECONDS,
    SmbClient,
    _vet_every_session,
    dial_address,
)
from app.utils.url_fetcher import PRIVATE_ADDRESS_SWITCH_ENV


def _resolving_to(*addresses: str):  # noqa: ANN202 - a patch context manager
    family = {4: socket.AF_INET, 6: socket.AF_INET6}
    return patch(
        "app.utils.url_fetcher.socket.getaddrinfo",
        return_value=[
            (family[6 if ":" in a else 4], socket.SOCK_STREAM, 6, "", (a, 445)) for a in addresses
        ],
    )


def test_open_file_shares_read_write_and_delete() -> None:
    client = SmbClient(server="files.example", username="u", password="p")
    fake = MagicMock()
    with _resolving_to("10.0.0.5"), patch.object(client, "_smbclient", return_value=fake):
        client.open_file("share", "dir/report.pptx")

    _, kwargs = fake.open_file.call_args
    assert fake.open_file.call_args.args[0] == r"\\10.0.0.5\share\dir\report.pptx"
    assert kwargs["mode"] == "rb"
    assert kwargs["share_access"] == "rwd"


def test_a_bracketed_ipv6_address_is_dialled_without_brackets() -> None:
    client = SmbClient(server="[fd00::5]", username="u", password="p")
    fake = MagicMock()
    with patch.object(client, "_smbclient", return_value=fake):
        client.register()

    assert fake.register_session.call_args.args[0] == "fd00::5"


def test_registering_waits_a_bounded_time_for_the_server() -> None:
    client = SmbClient(server="files.example", username="u", password="p")
    fake = MagicMock()
    with _resolving_to("10.0.0.5"), patch.object(client, "_smbclient", return_value=fake):
        client.register()

    assert fake.register_session.call_args.kwargs["connection_timeout"] == CONNECTION_TIMEOUT_SECONDS
    assert client._kwargs()["connection_timeout"] == CONNECTION_TIMEOUT_SECONDS


def test_the_session_is_opened_to_the_address_that_was_checked() -> None:
    client = SmbClient(server="files.example", username="u", password="p")
    fake = MagicMock()
    with _resolving_to("10.0.0.5"), patch.object(client, "_smbclient", return_value=fake):
        client.register()
    with _resolving_to("127.0.0.1"), patch.object(client, "_smbclient", return_value=fake):
        client.list_directory("share", "")

    fake.register_session.assert_called_once()
    assert fake.register_session.call_args.args[0] == "10.0.0.5"
    assert fake.scandir.call_args.args[0] == r"\\10.0.0.5\share"


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "127.8.9.1",
        "::1",
        "169.254.169.254",
        "fe80::1",
        "0.0.0.0",
        "224.0.0.1",
        "::ffff:127.0.0.1",
        "168.63.129.16",
        "100.100.100.200",
        "fd00:ec2::254",
    ],
)
def test_the_host_itself_link_local_and_cloud_metadata_addresses_are_refused(address: str) -> None:
    with _resolving_to("10.0.0.5", address), pytest.raises(NetworkShareAuthError, match="not allowed|never allowed|never connects"):
        dial_address("files.example")


@pytest.mark.parametrize("address", ["10.0.0.5", "192.168.1.20", "172.17.0.1", "fd00::5", "203.0.113.9"])
def test_private_and_public_addresses_are_allowed(address: str) -> None:
    with _resolving_to(address):
        assert dial_address("files.example") == address


def test_private_addresses_are_refused_where_the_deployment_blocks_them(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
    with _resolving_to("10.0.0.5"), pytest.raises(NetworkShareAuthError, match="private or internal"):
        dial_address("files.example")
    with _resolving_to("8.8.8.8"):
        assert dial_address("files.example") == "8.8.8.8"


def test_an_address_typed_in_is_not_looked_up() -> None:
    with patch("app.utils.url_fetcher.socket.getaddrinfo") as lookup:
        assert dial_address("10.0.0.5") == "10.0.0.5"
        with pytest.raises(NetworkShareAuthError, match="not allowed"):
            dial_address("127.0.0.1")
    lookup.assert_not_called()


def test_an_ipv4_address_is_preferred() -> None:
    with _resolving_to("fd00::5", "10.0.0.5"):
        assert dial_address("files.example") == "10.0.0.5"


def test_a_name_that_does_not_resolve_is_refused_before_connecting() -> None:
    client = SmbClient(server="nowhere.example", username="u", password="p")
    fake = MagicMock()
    with (
        patch("app.utils.url_fetcher.socket.getaddrinfo", side_effect=socket.gaierror("no such name")),
        patch.object(client, "_smbclient", return_value=fake),
        pytest.raises(NetworkShareAuthError, match="Could not find a server"),
    ):
        client.register()

    fake.register_session.assert_not_called()


def test_a_refused_address_never_reaches_smbclient() -> None:
    client = SmbClient(server="localhost", username="u", password="p")
    fake = MagicMock()
    with _resolving_to("127.0.0.1"), patch.object(client, "_smbclient", return_value=fake):
        with pytest.raises(NetworkShareAuthError, match="not allowed"):
            client.register()

    fake.register_session.assert_not_called()


def test_a_listing_keeps_the_servers_file_attributes() -> None:
    item = MagicMock()
    item.name = "pagefile.sys"
    item.is_dir.return_value = False
    item.is_symlink.return_value = False
    item.inode.return_value = 7
    item.smb_info.file_attributes = 0x6
    item.smb_info.end_of_file = 1
    item.smb_info.creation_time = None
    item.smb_info.last_write_time = None

    entry = SmbClient(server="files.example", username="u", password="p")._from_dir_entry(item)

    assert entry.attributes == 0x6


def test_a_dfs_referral_to_a_refused_address_never_opens_a_session() -> None:
    original = MagicMock()
    pool = SimpleNamespace(register_session=original)
    _vet_every_session(pool)
    _vet_every_session(pool)

    with _resolving_to("169.254.169.254"), pytest.raises(NetworkShareAuthError):
        pool.register_session("dfs-target.example", username="u")
    original.assert_not_called()

    with _resolving_to("10.0.0.7"):
        pool.register_session("dfs-target.example", username="u")
    original.assert_called_once_with("10.0.0.7", username="u")


def test_a_lookup_that_hangs_times_out() -> None:
    release = threading.Event()
    with (
        patch("app.sources.client.smb.smb.resolved_addresses", side_effect=lambda _: release.wait(5) and []),
        patch("app.sources.client.smb.smb.LOOKUP_TIMEOUT_SECONDS", 0.05),
        pytest.raises(NetworkShareAuthError, match="timed out"),
    ):
        dial_address("slow.example")
    release.set()


def _listing_raising(exc: Exception):  # noqa: ANN202 - a patch context manager
    fake = MagicMock()
    fake.scandir.side_effect = exc
    return fake


def test_a_folder_the_account_may_not_list_raises_access_denied() -> None:
    from smbprotocol.exceptions import SMBOSError
    from smbprotocol.header import NtStatus

    client = SmbClient(server="files.example", username="u", password="p")
    fake = _listing_raising(SMBOSError(NtStatus.STATUS_ACCESS_DENIED, r"\\10.0.0.5\share\private"))
    with _resolving_to("10.0.0.5"), patch.object(client, "_smbclient", return_value=fake):
        with pytest.raises(AccessDeniedError) as raised:
            client.list_directory("share", "private")

    assert (raised.value.share, raised.value.path) == ("share", "private")


def test_another_listing_failure_is_not_access_denied() -> None:
    from smbprotocol.exceptions import SMBOSError
    from smbprotocol.header import NtStatus

    client = SmbClient(server="files.example", username="u", password="p")
    fake = _listing_raising(SMBOSError(NtStatus.STATUS_IO_TIMEOUT, r"\\10.0.0.5\share\slow"))
    with _resolving_to("10.0.0.5"), patch.object(client, "_smbclient", return_value=fake):
        with pytest.raises(DirectoryListingError) as raised:
            client.list_directory("share", "slow")

    assert not isinstance(raised.value, AccessDeniedError)
