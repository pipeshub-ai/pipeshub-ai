"""Tests for app.sandbox.manager."""

import os
from unittest.mock import patch

import pytest

from app.sandbox.manager import (
    SandboxMode,
    get_executor,
    get_sandbox_mode,
    reset_executor,
)


class TestGetSandboxMode:
    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        assert get_sandbox_mode() == SandboxMode.LOCAL

    def test_local(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MODE", "local")
        assert get_sandbox_mode() == SandboxMode.LOCAL

    def test_docker(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        assert get_sandbox_mode() == SandboxMode.DOCKER

    def test_unknown_falls_back_to_local(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MODE", "kubernetes")
        assert get_sandbox_mode() == SandboxMode.LOCAL

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MODE", "DOCKER")
        assert get_sandbox_mode() == SandboxMode.DOCKER


class TestGetExecutor:
    def setup_method(self):
        reset_executor()

    def teardown_method(self):
        reset_executor()

    def test_returns_local_executor_by_default(self, monkeypatch):
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        executor = get_executor()
        from app.sandbox.local_executor import LocalExecutor
        assert isinstance(executor, LocalExecutor)

    def test_returns_docker_executor(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        executor = get_executor()
        from app.sandbox.docker_executor import DockerExecutor
        assert isinstance(executor, DockerExecutor)

    def test_singleton(self, monkeypatch):
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        e1 = get_executor()
        e2 = get_executor()
        assert e1 is e2

    def test_reset_clears_singleton(self, monkeypatch):
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        e1 = get_executor()
        reset_executor()
        e2 = get_executor()
        assert e1 is not e2
