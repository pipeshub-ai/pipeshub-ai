from unittest.mock import MagicMock, patch

from app.sources.client.smb.smb import SmbClient


def test_open_file_shares_read_write_and_delete() -> None:
    client = SmbClient(server="files.example", username="u", password="p")
    fake = MagicMock()
    with patch.object(client, "_smbclient", return_value=fake):
        client.open_file("share", "dir/report.pptx")

    _, kwargs = fake.open_file.call_args
    assert fake.open_file.call_args.args[0] == r"\\files.example\share\dir\report.pptx"
    assert kwargs["mode"] == "rb"
    assert kwargs["share_access"] == "rwd"
