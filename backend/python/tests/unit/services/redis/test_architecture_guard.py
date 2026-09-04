"""Architecture guard (Phase 0): no feature code constructs a Redis client
directly.

Every Redis-backed feature must depend on ``IRedisConnectionProvider``
(obtained via ``app.services.redis.get_redis_provider`` /
``RedisConnectionProviderFactory``), never ``redis.asyncio.Redis`` /
``redis.asyncio.cluster.RedisCluster`` / ``redis.Redis`` directly. That is
what lets a separate EE repo add AWS MemoryDB support by registering one
provider class -- no other file has to change.

This test statically scans every ``.py`` file under ``app/`` for a direct
``redis`` client import outside an explicit allow-list, so a new direct
import is caught in review rather than discovered when someone tries to
point the process at MemoryDB.

Importing exception types (``redis.exceptions.*``) is fine everywhere --
they are shared across every backend and provider implementation and never
construct a connection.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_APP_ROOT = Path(__file__).resolve().parents[4] / "app"

# Only the connection-provider implementations themselves may import a
# client class directly; everything else must go through
# `IRedisConnectionProvider`. Paths are relative to `app/`.
_ALLOWED_DIRECT_CLIENT_IMPORT_FILES = {
    "services/redis/standalone_provider.py",
    "services/redis/cluster_provider.py",
}

# Module names whose import, on its own, constructs or can construct a
# client connection and is therefore restricted to the allow-list above.
# `redis.exceptions`, `redis.crc`, and `redis.backoff` are deliberately not
# in this set -- they are pure types/helpers with no connection of their
# own, and are safe to import anywhere.
# Every module that hands out a client or a connection pool. The submodules
# matter as much as the packages: `redis.asyncio.client.Redis` *is*
# `redis.asyncio.Redis` (same class object), so listing only the package left
# a one-import bypass of this entire guard. Safe helpers -- `redis.exceptions`,
# `redis.asyncio.retry`, `redis.backoff`, `redis.crc` -- are deliberately
# absent: feature code may import those freely.
_RESTRICTED_MODULES = {
    "redis",
    "redis.client",
    "redis.cluster",
    "redis.connection",
    "redis.asyncio",
    "redis.asyncio.client",
    "redis.asyncio.cluster",
    "redis.asyncio.connection",
}


def _iter_app_python_files():
    for path in _APP_ROOT.rglob("*.py"):
        yield path


def _relative_path(path: Path) -> str:
    return path.relative_to(_APP_ROOT).as_posix()


def _find_restricted_imports(tree: ast.Module) -> list[tuple[int, str]]:
    """Return (lineno, module) for every restricted, non-TYPE_CHECKING import."""
    violations: list[tuple[int, str]] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._type_checking_depth = 0

        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            is_type_checking = (
                (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING")
                or (
                    isinstance(node.test, ast.Attribute)
                    and node.test.attr == "TYPE_CHECKING"
                )
            )
            if is_type_checking:
                self._type_checking_depth += 1
                for child in node.body:
                    self.visit(child)
                self._type_checking_depth -= 1
                for child in node.orelse:
                    self.visit(child)
            else:
                self.generic_visit(node)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            if self._type_checking_depth:
                return
            for alias in node.names:
                if alias.name == "redis" or alias.name.startswith("redis."):
                    if alias.name in _RESTRICTED_MODULES:
                        violations.append((node.lineno, alias.name))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            if self._type_checking_depth:
                return
            module = node.module or ""
            if module in _RESTRICTED_MODULES:
                # `from redis.asyncio import Redis` etc. Client classes only;
                # `from redis.exceptions import X` has module "redis.exceptions",
                # which is not in _RESTRICTED_MODULES, so it never lands here.
                violations.append((node.lineno, module))
            self.generic_visit(node)

    _Visitor().visit(tree)
    return violations


class TestNoDirectRedisClientImportsOutsideProviders:
    def test_only_the_provider_implementations_import_a_redis_client(self):
        offenders: list[str] = []
        for path in _iter_app_python_files():
            rel = _relative_path(path)
            if rel in _ALLOWED_DIRECT_CLIENT_IMPORT_FILES:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            except SyntaxError:
                continue
            for lineno, module in _find_restricted_imports(tree):
                offenders.append(f"{rel}:{lineno} imports '{module}'")

        assert not offenders, (
            "Found direct redis client imports outside the connection-provider "
            "allow-list. Route through app.services.redis.get_redis_provider() "
            "instead:\n" + "\n".join(offenders)
        )

    def test_allow_list_files_still_exist(self):
        """Catches a rename of a provider file without updating the allow-list."""
        for rel in _ALLOWED_DIRECT_CLIENT_IMPORT_FILES:
            assert (_APP_ROOT / rel).is_file(), f"{rel} no longer exists"

    def test_exception_imports_are_never_flagged(self):
        """redis.exceptions / redis.crc / redis.backoff are always allowed;
        this pins that `_RESTRICTED_MODULES` does not accidentally grow to
        include them."""
        assert "redis.exceptions" not in _RESTRICTED_MODULES
        assert "redis.crc" not in _RESTRICTED_MODULES
        assert "redis.backoff" not in _RESTRICTED_MODULES


class TestTheGuardItselfCatchesClientSubmodules:
    """The guard is only worth having if it cannot be sidestepped.

    `redis.asyncio.client.Redis` is the *same class object* as
    `redis.asyncio.Redis`, so matching only the package left a one-line
    bypass: any file could construct a client directly and still pass.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "from redis.asyncio.client import Redis",
            "from redis.client import Redis",
            "from redis.cluster import RedisCluster",
            "from redis.asyncio.connection import BlockingConnectionPool",
            "from redis.connection import ConnectionPool",
            "import redis.asyncio.client",
            "from redis.asyncio import Redis",
            "import redis",
        ],
    )
    def test_a_client_import_is_flagged(self, source: str) -> None:
        assert _find_restricted_imports(ast.parse(source)), (
            f"{source!r} bypasses the architecture guard"
        )

    @pytest.mark.parametrize(
        "source",
        [
            "from redis.exceptions import ConnectionError",
            "from redis.asyncio.retry import Retry",
            "from redis.backoff import ExponentialBackoff",
            "from redis.crc import key_slot",
        ],
    )
    def test_safe_helpers_are_not_flagged(self, source: str) -> None:
        """Feature code needs these; flagging them would make the guard
        unusable and invite a blanket allow-list."""
        assert _find_restricted_imports(ast.parse(source)) == []

    def test_a_type_checking_only_import_is_not_flagged(self) -> None:
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from redis.asyncio.client import Redis\n"
        )
        assert _find_restricted_imports(ast.parse(source)) == []
