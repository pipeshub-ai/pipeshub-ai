"""The resilience fault switches: each one takes its dependency away and puts it back exactly.

The snippets run in the app container during the nightly; here they run in a
temporary folder with the same shell, so a switch that fails to undo itself
is caught before it can leave the shared stack broken.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from helper.fault_switches import (
    ai_provider_hosts,
    block_hosts_script,
    restore_hosts_script,
    storage_off_script,
    storage_on_script,
)
from helper.plain_message import plain_language_problems

pytestmark = pytest.mark.unit


def _sh(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=False)


PUBLIC_PROVIDERS = ["api.openai.com", "generativelanguage.googleapis.com", "api.groq.com"]


def _host_entries(text: str) -> list[tuple[str, str]]:
    """The (address, host name) pairs a hosts file maps, one per name."""
    entries = []
    for line in text.splitlines():
        fields = line.split("#", 1)[0].split()
        entries += [(fields[0], name) for name in fields[1:]]
    return entries


def test_provider_hosts_are_the_public_providers_without_an_endpoint() -> None:
    assert ai_provider_hosts({}) == PUBLIC_PROVIDERS


def test_provider_hosts_add_the_configured_azure_endpoint_once() -> None:
    expected = [*PUBLIC_PROVIDERS, "acme-ai.openai.azure.com"]
    assert ai_provider_hosts({"TEST_AZURE_OPENAI_ENDPOINT": "https://acme-ai.openai.azure.com/"}) == expected
    assert ai_provider_hosts({"TEST_AZURE_OPENAI_ENDPOINT": "acme-ai.openai.azure.com"}) == expected


def test_blocking_refuses_bad_host_names() -> None:
    with pytest.raises(ValueError):
        block_hosts_script(["api.openai.com; rm -rf /"])
    with pytest.raises(ValueError):
        block_hosts_script([])


def test_hosts_are_blocked_and_restored_byte_for_byte(tmp_path: Path) -> None:
    hosts, backup = tmp_path / "hosts", tmp_path / "hosts.bak"
    original = "127.0.0.1 localhost\n10.0.0.5 mongodb\n"
    hosts.write_text(original)

    assert _sh(block_hosts_script(["api.openai.com"], hosts_file=str(hosts), backup=str(backup))).returncode == 0
    blocked = hosts.read_text()
    assert blocked.startswith(original)
    assert _host_entries(blocked) == [
        *_host_entries(original),
        ("127.0.0.1", "api.openai.com"),
        ("::1", "api.openai.com"),
    ]
    # Blocking twice must not stack entries or overwrite the saved original.
    assert _sh(block_hosts_script(["api.openai.com"], hosts_file=str(hosts), backup=str(backup))).returncode == 0
    assert hosts.read_text() == blocked

    assert _sh(restore_hosts_script(hosts_file=str(hosts), backup=str(backup))).returncode == 0
    assert hosts.read_text() == original
    assert not backup.exists()
    # Restoring when nothing was blocked changes nothing.
    assert _sh(restore_hosts_script(hosts_file=str(hosts), backup=str(backup))).returncode == 0
    assert hosts.read_text() == original


def test_storage_is_made_unwritable_and_restored_with_its_files(tmp_path: Path) -> None:
    live = tmp_path / "PipesHub"
    (live / "org").mkdir(parents=True)
    (live / "org" / "doc.md").write_text("kept")

    assert _sh(storage_off_script(str(tmp_path), "PipesHub")).returncode == 0
    assert live.is_file()
    assert _sh(f"mkdir -p {live}/org2").returncode != 0, "a write under the storage folder still worked"

    assert _sh(storage_on_script(str(tmp_path), "PipesHub")).returncode == 0
    assert (live / "org" / "doc.md").read_text() == "kept"
    assert _sh(storage_on_script(str(tmp_path), "PipesHub")).returncode == 0
    assert (live / "org" / "doc.md").read_text() == "kept"


def test_storage_off_changes_nothing_when_there_is_no_storage_folder(tmp_path: Path) -> None:
    assert _sh(storage_off_script(str(tmp_path), "PipesHub")).returncode != 0
    assert not (tmp_path / "PipesHub").exists()


@pytest.mark.parametrize(
    "message",
    [
        "Connection error.",
        "Transient failure, retry scheduled: Connection error.",
        "Enrichment failed: APIConnectionError('Connection error.')",
        "Request failed with status code 500",
        "ENOTDIR: not a directory, mkdir '/root/.local/PipesHub/org'",
        "",
    ],
)
def test_raw_error_text_does_not_read_plainly(message: str) -> None:
    assert plain_language_problems(message, about=("AI", "model", "upload", "file"))


def test_a_plain_message_with_a_next_step_reads_plainly() -> None:
    message = (
        "We couldn't reach your AI model provider, so this file wasn't indexed. "
        "Check the provider's status or your AI Models settings, then try again."
    )
    assert plain_language_problems(message, about=("AI", "model")) == []
