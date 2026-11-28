"""R38IDENT-FU-8 (ADR-KEY-02 D8): each SECRET_KEY ring member may come from a file (a Docker or Kubernetes secret).

A named file wins over the variable and loses one trailing line break; a file that cannot be read or is empty stops
the start with the variable and the path, never the content; the sandbox plane refuses the file variables as it
refuses the values.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import TYPE_CHECKING

import pytest

from app.sandbox.docker_proxy import proxy_callers
from app.utils.encryption.component_key import ComponentKeyError, component_identity
from app.utils.encryption.secret_key_ring import (
    PLATFORM_KEY_ENVS,
    SecretKeyRingError,
    deployment_ring,
    platform_keys_held,
    ring_secret,
    secret_key_configured,
    secret_key_ring,
)

if TYPE_CHECKING:
    from pathlib import Path

STRONG = hashlib.sha256(b"fu-8 strong").hexdigest()
OTHER = hashlib.sha256(b"fu-8 other").hexdigest()
COMPONENT_KEY = hashlib.sha256(b"fu-8 component").hexdigest()


def _key_file(tmp_path: Path, content: str, name: str = "secret-key", mode: int = 0o400) -> str:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return str(path)


def test_the_file_is_read_and_wins_over_the_variable(tmp_path: Path) -> None:
    path = _key_file(tmp_path, f"{STRONG}\n")
    ring = secret_key_ring({"SECRET_KEY_FILE": path, "SECRET_KEY": OTHER})
    assert ring.current == STRONG
    assert ring.other is None


def test_the_variable_still_works_without_a_file() -> None:
    assert secret_key_ring({"SECRET_KEY": STRONG}).current == STRONG


@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_one_trailing_line_break_is_dropped(tmp_path: Path, ending: str) -> None:
    assert ring_secret("SECRET_KEY", {"SECRET_KEY_FILE": _key_file(tmp_path, STRONG + ending)}) == STRONG


@pytest.mark.parametrize("content", [f" {STRONG}\n", f"{STRONG}\n\n", f"{STRONG} \n", f"\t{STRONG}"])
def test_other_whitespace_is_the_keys_and_refused(tmp_path: Path, content: str) -> None:
    with pytest.raises(SecretKeyRingError, match="starts or ends with a space or line break"):
        secret_key_ring({"SECRET_KEY_FILE": _key_file(tmp_path, content)})


def test_a_missing_file_names_the_variable_and_the_path(tmp_path: Path) -> None:
    missing = str(tmp_path / "nope")
    with pytest.raises(SecretKeyRingError) as caught:
        secret_key_ring({"SECRET_KEY_FILE": missing, "SECRET_KEY": STRONG})
    assert str(caught.value) == f"SECRET_KEY_FILE names {missing}, which cannot be read (FileNotFoundError)"


def test_an_empty_file_is_refused_not_read_as_unset(tmp_path: Path) -> None:
    path = _key_file(tmp_path, "\n")
    with pytest.raises(SecretKeyRingError, match=f"SECRET_KEY_FILE names {path}, which is empty"):
        secret_key_ring({"SECRET_KEY_FILE": path, "SECRET_KEY": STRONG})


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
def test_an_unreadable_file_is_refused_and_never_shows_the_key(tmp_path: Path) -> None:
    path = _key_file(tmp_path, STRONG, mode=0o000)
    with pytest.raises(SecretKeyRingError) as caught:
        secret_key_ring({"SECRET_KEY_FILE": path})
    assert "PermissionError" in str(caught.value)
    assert STRONG not in str(caught.value)


def test_a_weak_key_in_a_file_is_refused_like_one_in_the_variable(tmp_path: Path) -> None:
    with pytest.raises(SecretKeyRingError) as caught:
        secret_key_ring({"SECRET_KEY_FILE": _key_file(tmp_path, "your_random_encryption_secret_key\n")})
    assert str(caught.value).startswith("SECRET_KEY is a published placeholder")


def test_every_ring_member_may_come_from_a_file(tmp_path: Path) -> None:
    current = _key_file(tmp_path, STRONG, "current")
    previous = _key_file(tmp_path, "weak-old-key\n", "previous")
    ring = secret_key_ring({"SECRET_KEY_FILE": current, "SECRET_KEY_PREVIOUS_FILE": previous})
    assert (ring.current, ring.other, ring.other_role) == (STRONG, "weak-old-key", "previous")
    upcoming = _key_file(tmp_path, OTHER, "next")
    ring = secret_key_ring({"SECRET_KEY_FILE": current, "SECRET_KEY_NEXT_FILE": upcoming})
    assert (ring.other, ring.other_role) == (OTHER, "next")
    with pytest.raises(SecretKeyRingError, match="never both"):
        secret_key_ring({"SECRET_KEY_FILE": current, "SECRET_KEY_NEXT_FILE": upcoming, "SECRET_KEY_PREVIOUS": "old"})


def test_a_world_readable_file_is_used_with_a_warning_that_shows_no_key(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _key_file(tmp_path, STRONG, "world", mode=0o644)
    with caplog.at_level(logging.WARNING):
        assert secret_key_ring({"SECRET_KEY_FILE": path}).current == STRONG
    warned = [r for r in caplog.records if "every account can read" in r.getMessage()]
    assert [r.getMessage() for r in warned] == ["SECRET_KEY_FILE names a file every account can read: make it mode 0400"]
    # neither the key nor where it lives reaches the record, formatted or in its arguments
    for r in caplog.records:
        assert STRONG not in r.getMessage() and path not in r.getMessage()
        assert all(STRONG not in str(a) and path not in str(a) for a in (r.args or ()))


def test_the_process_ring_and_its_checks_see_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PLATFORM_KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    assert not secret_key_configured()
    assert deployment_ring() is None
    monkeypatch.setenv("SECRET_KEY_FILE", _key_file(tmp_path, STRONG))
    assert secret_key_configured()
    ring = deployment_ring()
    assert ring is not None and ring.current == STRONG


@pytest.mark.parametrize("held", [n for n in PLATFORM_KEY_ENVS if n.endswith("_FILE")])
def test_a_component_refuses_the_root_key_by_file_too(held: str) -> None:
    env = {"PIPESHUB_COMPONENT": "egress-proxy", "PIPESHUB_COMPONENT_KEY": COMPONENT_KEY, held: "/run/secrets/k"}
    assert platform_keys_held(env) == [held]
    with pytest.raises(ComponentKeyError, match=f"Remove {held} from its environment"):
        component_identity(env)


@pytest.mark.parametrize("held", [n for n in PLATFORM_KEY_ENVS if n.endswith("_FILE")])
def test_the_docker_api_proxy_refuses_the_root_key_by_file_too(held: str) -> None:
    with pytest.raises(RuntimeError, match=held):
        proxy_callers({"DOCKER_PROXY_BROKER_TOKEN": COMPONENT_KEY, held: "/run/secrets/k"})
