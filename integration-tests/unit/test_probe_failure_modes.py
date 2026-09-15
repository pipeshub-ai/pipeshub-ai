"""A probe that could not look must never answer "nothing is there".

Every assertion in the cleanup suite passes when a store reports empty, so the
one thing these probes must never do is return empty because something went
wrong. Each test below is a way that used to happen.
"""

from __future__ import annotations

import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from helper.blob_store import BlobProbeUnavailable, BlobStoreProbe
from helper.vector_store import VectorProbeUnavailable, VectorStoreProbe

pytestmark = pytest.mark.unit


def _completed(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr
    )


# --------------------------------------------------------------------- #
# Finding the container
# --------------------------------------------------------------------- #


def test_the_single_running_app_container_is_used() -> None:
    probe = BlobStoreProbe()
    with patch.object(
        probe, "_run_docker",
        return_value=_completed(0, "pipeshub-ci-pipeshub-ai-1\npipeshub-ci-redis-1\n"),
    ):
        assert probe.container() == "pipeshub-ci-pipeshub-ai-1"


def test_no_matching_container_raises_rather_than_reporting_empty() -> None:
    """Compose does not pin container_name, so a guessed name finds nothing.

    Returning "no files" here would be a green cleanup test that never looked
    at blob storage at all.
    """
    probe = BlobStoreProbe()
    with patch.object(probe, "_run_docker", return_value=_completed(0, "some-other-1\n")):
        with pytest.raises(BlobProbeUnavailable, match="No running container"):
            probe.container()


def test_several_matching_containers_raise_instead_of_guessing() -> None:
    """More than one stack can be up on a developer machine."""
    probe = BlobStoreProbe()
    listing = "a-pipeshub-ai-1\nb-pipeshub-ai-1\n"
    with patch.object(probe, "_run_docker", return_value=_completed(0, listing)):
        with pytest.raises(BlobProbeUnavailable, match="Several containers"):
            probe.container()


def test_an_explicit_container_skips_discovery() -> None:
    probe = BlobStoreProbe(container="named-explicitly")
    assert probe.container() == "named-explicitly"


# --------------------------------------------------------------------- #
# Listing files
# --------------------------------------------------------------------- #


def test_a_missing_directory_is_an_empty_answer() -> None:
    """The directory being gone is what a successful cleanup looks like."""
    probe = BlobStoreProbe(container="c")
    with patch.object(probe, "_run_docker", return_value=_completed(3)):
        assert probe._local_files_under("org/records/v1") == []


def test_files_are_returned_when_the_directory_is_there() -> None:
    probe = BlobStoreProbe(container="c")
    listing = "/root/.local/PipesHub/org/records/v1/current/a.json\n"
    with patch.object(probe, "_run_docker", return_value=_completed(0, listing)):
        assert len(probe._local_files_under("org/records/v1")) == 1


def test_a_failed_listing_raises_rather_than_reporting_no_files() -> None:
    """Permission or mount errors used to read as a clean store."""
    probe = BlobStoreProbe(container="c")
    with patch.object(
        probe, "_run_docker", return_value=_completed(1, stderr="permission denied")
    ):
        with pytest.raises(BlobProbeUnavailable, match="Could not list blob storage"):
            probe._local_files_under("org/records/v1")


# --------------------------------------------------------------------- #
# Counting points
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_collection_that_vanished_mid_scan_is_skipped() -> None:
    """Listed, then dropped before the count. It holds nothing, which is the answer."""
    probe = VectorStoreProbe(host="localhost")
    client = MagicMock()
    client.count = AsyncMock(side_effect=Exception("Collection `x` not found"))
    probe._conn = AsyncMock(return_value=client)
    probe.collections = AsyncMock(return_value=["x"])

    assert await probe.count_for_virtual_record("v1") == 0


@pytest.mark.asyncio
async def test_a_transport_failure_raises_rather_than_counting_zero() -> None:
    """Zero from a failed count is read as "the store is clean"."""
    probe = VectorStoreProbe(host="localhost")
    client = MagicMock()
    client.count = AsyncMock(side_effect=Exception("Connection refused"))
    probe._conn = AsyncMock(return_value=client)
    probe.collections = AsyncMock(return_value=["records"])

    with pytest.raises(VectorProbeUnavailable, match="Could not count points"):
        await probe.count_for_virtual_record("v1")


# --------------------------------------------------------------------- #
# Transport security
# --------------------------------------------------------------------- #


def test_an_api_key_over_plaintext_to_a_remote_host_is_refused() -> None:
    with pytest.raises(ValueError, match="cleartext"):
        VectorStoreProbe(host="qdrant.example.com", api_key="secret")


def test_an_api_key_over_plaintext_to_localhost_is_fine() -> None:
    """The integration stack publishes Qdrant on loopback without TLS."""
    VectorStoreProbe(host="localhost", api_key="secret")
    VectorStoreProbe(host="127.0.0.1", api_key="secret")


def test_a_remote_host_with_tls_is_fine() -> None:
    VectorStoreProbe(host="qdrant.example.com", api_key="secret", use_https=True)


def test_a_remote_host_without_a_key_is_fine() -> None:
    VectorStoreProbe(host="qdrant.example.com", api_key="")
