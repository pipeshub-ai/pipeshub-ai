from unittest.mock import MagicMock, patch

from app.sources.client.smb.smb import CONNECTION_TIMEOUT_SECONDS, SmbClient


def test_open_file_shares_read_write_and_delete() -> None:
    client = SmbClient(server="files.example", username="u", password="p")
    fake = MagicMock()
    with patch.object(client, "_smbclient", return_value=fake):
        client.open_file("share", "dir/report.pptx")

    _, kwargs = fake.open_file.call_args
    assert fake.open_file.call_args.args[0] == r"\\files.example\share\dir\report.pptx"
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
    with patch.object(client, "_smbclient", return_value=fake):
        client.register()

    assert fake.register_session.call_args.kwargs["connection_timeout"] == CONNECTION_TIMEOUT_SECONDS
    assert client._kwargs()["connection_timeout"] == CONNECTION_TIMEOUT_SECONDS


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
