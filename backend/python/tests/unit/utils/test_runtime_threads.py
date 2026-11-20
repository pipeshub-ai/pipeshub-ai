"""Unit tests for app.utils.runtime_threads."""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import patch

import pytest

import app.utils

_MODULE = "app.utils.runtime_threads"


@pytest.fixture(autouse=True)
def _fresh_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test imports the module again so its body re-runs; afterwards the original entry and the package attribute
    are put back, so the rest of the process keeps one copy."""
    monkeypatch.delitem(sys.modules, _MODULE, raising=False)
    if hasattr(app.utils, "runtime_threads"):
        monkeypatch.setattr(app.utils, "runtime_threads", app.utils.runtime_threads)


def _reload_module() -> object:
    """Import runtime_threads afresh so module-level code re-runs."""
    return importlib.import_module(_MODULE)


class TestEnvDefaults:
    def test_sets_omp_when_absent(self):
        keys = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "TOKENIZERS_PARALLELISM")
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            with patch.dict(os.environ, {}, clear=False):
                _reload_module()
                assert os.environ["OMP_NUM_THREADS"] == "2"
                assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
                assert os.environ["MKL_NUM_THREADS"] == "2"
                assert os.environ["TOKENIZERS_PARALLELISM"] == "false"
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_does_not_overwrite_operator_value(self):
        """An already-set OMP_NUM_THREADS must be respected."""
        saved = os.environ.get("OMP_NUM_THREADS")
        os.environ["OMP_NUM_THREADS"] = "8"
        try:
            _reload_module()
            assert os.environ["OMP_NUM_THREADS"] == "8"
        finally:
            if saved is None:
                os.environ.pop("OMP_NUM_THREADS", None)
            else:
                os.environ["OMP_NUM_THREADS"] = saved

    def test_openblas_mirrors_omp_when_omp_preset(self):
        """OPENBLAS_NUM_THREADS defaults to whatever OMP_NUM_THREADS is."""
        saved_omp = os.environ.get("OMP_NUM_THREADS")
        saved_blas = os.environ.pop("OPENBLAS_NUM_THREADS", None)
        os.environ["OMP_NUM_THREADS"] = "4"
        try:
            with patch.dict(os.environ, {}, clear=False):
                _reload_module()
                assert os.environ["OPENBLAS_NUM_THREADS"] == "4"
        finally:
            if saved_omp is None:
                os.environ.pop("OMP_NUM_THREADS", None)
            else:
                os.environ["OMP_NUM_THREADS"] = saved_omp
            if saved_blas is None:
                os.environ.pop("OPENBLAS_NUM_THREADS", None)
            else:
                os.environ["OPENBLAS_NUM_THREADS"] = saved_blas
