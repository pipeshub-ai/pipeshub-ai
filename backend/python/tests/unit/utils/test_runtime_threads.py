"""Unit tests for app.utils.runtime_threads."""

from __future__ import annotations

import importlib
import os
import sys
from unittest.mock import patch


def _reload_module() -> object:
    """Reload runtime_threads so module-level code re-runs."""
    sys.modules.pop("app.utils.runtime_threads", None)
    return importlib.import_module("app.utils.runtime_threads")


class TestEnvDefaults:
    def test_sets_omp_when_absent(self):
        keys = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "TOKENIZERS_PARALLELISM")
        saved = {k: os.environ.pop(k, None) for k in keys}
        try:
            sys.modules.pop("app.utils.runtime_threads", None)
            with patch.dict(os.environ, {}, clear=False):
                importlib.import_module("app.utils.runtime_threads")
                assert os.environ["OMP_NUM_THREADS"] == "2"
                assert os.environ["OPENBLAS_NUM_THREADS"] == "2"
                assert os.environ["MKL_NUM_THREADS"] == "2"
                assert os.environ["TOKENIZERS_PARALLELISM"] == "false"
        finally:
            sys.modules.pop("app.utils.runtime_threads", None)
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
            sys.modules.pop("app.utils.runtime_threads", None)
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
            sys.modules.pop("app.utils.runtime_threads", None)
            with patch.dict(os.environ, {}, clear=False):
                importlib.import_module("app.utils.runtime_threads")
                assert os.environ["OPENBLAS_NUM_THREADS"] == "4"
        finally:
            sys.modules.pop("app.utils.runtime_threads", None)
            if saved_omp is None:
                os.environ.pop("OMP_NUM_THREADS", None)
            else:
                os.environ["OMP_NUM_THREADS"] = saved_omp
            if saved_blas is None:
                os.environ.pop("OPENBLAS_NUM_THREADS", None)
            else:
                os.environ["OPENBLAS_NUM_THREADS"] = saved_blas
