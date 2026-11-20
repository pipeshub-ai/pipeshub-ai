"""A build that leaves connectors out of its registry has their suites skipped, not failed."""

from __future__ import annotations

from pathlib import Path

import pytest

from helper import connectors_offered

pytestmark = pytest.mark.unit


def _extension(root: Path, body: str) -> None:
    directory = root / "downstream" / "suite_extensions"
    directory.mkdir(parents=True)
    (directory / "connectors_not_offered.py").write_text(body)


def test_without_an_extension_every_connector_is_offered(tmp_path: Path) -> None:
    assert connectors_offered.not_offered(tmp_path) == {}
    assert connectors_offered.skipped_markers(tmp_path) == {}
    connectors_offered.skip_if_not_offered("MinIO", tmp_path)


def test_a_connector_left_out_skips_its_suites_and_the_tests_that_create_it(tmp_path: Path) -> None:
    _extension(tmp_path, 'NOT_OFFERED = {"MinIO": (("minio", "minio_sim"), "not in this build")}\n')

    reasons = connectors_offered.skipped_markers(tmp_path)

    assert set(reasons) == {"minio", "minio_sim"}
    assert "MinIO" in reasons["minio"] and "not in this build" in reasons["minio"]
    with pytest.raises(pytest.skip.Exception, match="does not offer the MinIO connector"):
        connectors_offered.skip_if_not_offered("MinIO", tmp_path)
    connectors_offered.skip_if_not_offered("S3", tmp_path)
