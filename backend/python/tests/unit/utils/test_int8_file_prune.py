"""The manual helper that lists old int8 files: exact names, the model checked twice, young files left alone."""

import json
import os
import time
from pathlib import Path

import torch
from safetensors.torch import save_file

from app.utils.embedding_int8_prune import MIN_AGE_SECONDS, parse_name, stale_files


def _file(cache: Path, name: str, model: str, age: float = 3 * MIN_AGE_SECONDS) -> Path:
    path = cache / name
    save_file({"x": torch.zeros(1)}, str(path), metadata={"identity": json.dumps({"model": model})})
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def _name(model: str, rev: str = "a" * 12, sha: str = "b" * 16, fmt: str = "1") -> str:
    return f"{model}-{rev}-{sha}-v{fmt}.safetensors"


class TestNames:
    def test_only_the_exact_pattern_parses(self) -> None:
        assert parse_name(_name("BAAI_bge-large-en-v1.5")) == ("BAAI_bge-large-en-v1.5", "a" * 12, "b" * 16, "1")
        for bad in ("x.safetensors", "m-abc-def-v1.safetensors", f"m-{'a' * 12}-{'b' * 16}-v1.bin", f"m-{'A' * 12}-{'b' * 16}-v1.safetensors"):
            assert parse_name(bad) is None


class TestSelection:
    def test_another_revision_of_the_same_model_and_format_is_listed(self, tmp_path) -> None:
        keep = _file(tmp_path, _name("m"), "m")
        old = _file(tmp_path, _name("m", rev="c" * 12), "m")
        assert stale_files(tmp_path, "m", keep) == [old]

    def test_a_model_whose_name_starts_the_same_is_never_selected(self, tmp_path) -> None:
        keep = _file(tmp_path, _name("BAAI_bge-small-en"), "BAAI/bge-small-en")
        _file(tmp_path, _name("BAAI_bge-small-en-v1.5", rev="c" * 12), "BAAI/bge-small-en-v1.5")
        assert stale_files(tmp_path, "BAAI/bge-small-en", keep) == []

    def test_two_ids_that_sanitise_to_one_name_are_told_apart_by_the_stored_identity(self, tmp_path) -> None:
        keep = _file(tmp_path, _name("a_b"), "a/b")
        _file(tmp_path, _name("a_b", rev="c" * 12), "a_b")  # same name part, another model inside
        mine = _file(tmp_path, _name("a_b", rev="d" * 12), "a/b")
        assert stale_files(tmp_path, "a/b", keep) == [mine]

    def test_young_files_other_formats_and_the_kept_file_are_never_selected(self, tmp_path) -> None:
        keep = _file(tmp_path, _name("m"), "m")
        _file(tmp_path, _name("m", rev="c" * 12), "m", age=60)
        _file(tmp_path, _name("m", rev="d" * 12, fmt="2"), "m")
        assert stale_files(tmp_path, "m", keep) == []

    def test_a_file_that_cannot_be_read_is_left_alone(self, tmp_path) -> None:
        keep = _file(tmp_path, _name("m"), "m")
        junk = tmp_path / _name("m", rev="c" * 12)
        junk.write_bytes(b"\x00" * 16)
        old = time.time() - 3 * MIN_AGE_SECONDS
        os.utime(junk, (old, old))
        assert stale_files(tmp_path, "m", keep) == []
