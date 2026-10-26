"""The protobuf runtime and everything that serialises with it.

The manifest does not import protobuf; the libraries below do, and several of
them ship generated ``*_pb2`` modules. A runtime they cannot load fails at
import time, in the service that happens to import them first, so each is
imported and round-tripped here. The manifest must also resolve for every
Python that ``requires-python`` admits, or a universal lock cannot be made.
"""

import importlib
import importlib.metadata
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

MANIFEST = Path(__file__).resolve().parents[2] / "pyproject.toml"

# Third-party packages that load protobuf, and the app modules that import them.
PROTOBUF_USERS = [
    "etcd3",
    "grpc",
    "grpc_status.rpc_status",
    "google.api_core.exceptions",
    "google.cloud.storage",
    "google.rpc.status_pb2",
    "onnxruntime",
    "opensearchpy",
    "proto",
    "qdrant_client",
    "qdrant_client.grpc",
    "snowflake.connector",
]
APP_MODULES = [
    "app.config.providers.etcd.etcd3_store",
    "app.config.providers.etcd.etcd3_encrypted_store",
    "app.services.vector_db.qdrant.qdrant",
    "app.services.vector_db.opensearch.opensearch",
    "app.sources.client.gcs.gcs",
    "app.sources.client.snowflake.snowflake",
]


def _lowest_python() -> str:
    spec = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["project"]["requires-python"]
    return next(s.version for s in SpecifierSet(spec) if s.operator == ">=")


def _protobuf_requirement() -> Requirement:
    dependencies = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))["project"]["dependencies"]
    return next(Requirement(d) for d in dependencies if Requirement(d).name == "protobuf")


def test_the_installed_runtime_satisfies_the_manifest() -> None:
    assert Version(importlib.metadata.version("protobuf")) in _protobuf_requirement().specifier


@pytest.mark.parametrize("module", PROTOBUF_USERS + APP_MODULES)
def test_a_module_that_loads_protobuf_imports(module: str) -> None:
    importlib.import_module(module)


def test_generated_messages_round_trip() -> None:
    from etcd3.etcdrpc import rpc_pb2
    from google.protobuf import json_format, struct_pb2
    from google.rpc import code_pb2, status_pb2
    from qdrant_client.grpc import collections_pb2

    for message in (
        rpc_pb2.PutRequest(key=b"k", value=b"v"),
        collections_pb2.CreateCollection(collection_name="records"),
        status_pb2.Status(code=code_pb2.NOT_FOUND, message="missing"),
    ):
        assert type(message).FromString(message.SerializeToString()) == message

    parsed = json_format.ParseDict({"a": [1, "b"]}, struct_pb2.Struct())
    assert json_format.MessageToDict(parsed) == {"a": [1.0, "b"]}


def test_proto_plus_messages_round_trip() -> None:
    import proto

    class Note(proto.Message):
        text = proto.Field(proto.STRING, number=1)

    assert Note.deserialize(Note.serialize(Note(text="z"))).text == "z"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_the_manifest_resolves_for_every_supported_python() -> None:
    """A universal resolution covers each Python that requires-python admits.
    Before the protobuf pin was lifted it failed for Python 3.14, where the
    Google libraries need protobuf 6, which kept any lockfile from being made."""
    result = subprocess.run(
        [
            "uv", "pip", "compile", str(MANIFEST), "--universal", "--quiet", "--output-file", "/dev/null",
            # Universal mode starts at the running Python, not at the manifest's lower bound.
            "--python-version", _lowest_python(),
        ],
        capture_output=True, text=True, timeout=600, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_the_integration_tests_lock_matches_its_manifest() -> None:
    """integration-tests/uv.lock is checked in and imports app code, so it has to
    move with the protobuf pin."""
    project = MANIFEST.parents[2] / "integration-tests"
    if not (project / "uv.lock").exists():
        pytest.skip("integration-tests/ is not part of this checkout")
    result = subprocess.run(
        ["uv", "lock", "--check"], cwd=project, capture_output=True, text=True, timeout=600, check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
