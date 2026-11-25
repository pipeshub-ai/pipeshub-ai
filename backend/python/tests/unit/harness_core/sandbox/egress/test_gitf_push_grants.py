"""GITF-23 (WP-63 rule): the egress proxy's one-push grants as a unit. A grant is issued only for a fresh
placeholder, a repository the sandbox's git rule names, a sealed credential and a short life; it is good for that
sandbox, that repository's push and one ``git-receive-pack``; spent, expired or presented by another sandbox it is
refused, and it never serves a fetch."""

from __future__ import annotations

import pytest

from harness_core.sandbox.egress import (
    EgressBinding,
    EgressRefusalReason,
    EgressRefused,
    ProxyRequest,
)
from harness_core.sandbox.egress.push_grants import (
    MAX_TTL_S,
    PushGrantInvalid,
    PushGrants,
)
from harness_core.sandbox.egress.sealed import CredentialSealer, new_placeholder
from harness_core.sandbox.egress.types import Target
from harness_core.sandbox.types import EgressDestination, EgressPolicy, GitAccess
from harness_core.testing.clock import ManualClock

R = EgressRefusalReason
HOST = "git.example.test"
REPO = "acme/widgets"
SEALER = CredentialSealer(b"gitf-23")
ACCESS = GitAccess(REPO)


def _binding(sandbox_id: str = "sbx-1") -> EgressBinding:
    policy = EgressPolicy(destinations=(EgressDestination(HOST, (80,), git=ACCESS),))
    return EgressBinding(sandbox_id, "10.0.0.1", "10.0.0.2", policy, {"runId": "r1"})


def _issue(grants: PushGrants, binding: EgressBinding | None = None, **change: object) -> str:
    placeholder = str(change.pop("placeholder", new_placeholder()))
    fields: dict[str, object] = {
        "placeholder": placeholder, "host": HOST, "repository": REPO, "branch": "pipeshub/session-s1",
        "credential_ref": SEALER.seal(placeholder, "u:push"), "ttl_s": 120, **change,
    }
    _ = grants.issue(binding or _binding(), **fields)  # type: ignore[arg-type]
    return placeholder


def _request(placeholder: str, method: str = "POST", path: str = f"/{REPO}.git/git-receive-pack") -> ProxyRequest:
    import base64

    auth = "Basic " + base64.b64encode(f"pxh:{placeholder}".encode()).decode()
    return ProxyRequest(method=method, target=f"http://{HOST}{path}", headers=(("Host", HOST), ("Authorization", auth)), body=b"")


def _target(path: str = f"/{REPO}.git/git-receive-pack") -> Target:
    return Target(HOST, 80, path)


def test_gitf23_a_grant_serves_its_push_once_then_refuses_replays_and_expiry() -> None:
    clock = ManualClock()
    grants = PushGrants(clock)
    placeholder = _issue(grants)
    refs = _request(placeholder, "GET", f"/{REPO}.git/info/refs?service=git-receive-pack")
    access, route = grants.admit(refs, _target(f"/{REPO}.git/info/refs?service=git-receive-pack"), _binding(), ACCESS) or (None, None)
    assert access == GitAccess(REPO, push_branch="pipeshub/session-s1") and route is not None
    assert route.placeholder == placeholder and SEALER.open(route) == "u:push"
    assert grants.admit(_request(placeholder), _target(), _binding(), ACCESS) is not None
    with pytest.raises(EgressRefused) as replay:
        _ = grants.admit(_request(placeholder), _target(), _binding(), ACCESS)
    assert replay.value.reason is R.TOKEN_REPLAYED
    late = _issue(grants)
    clock.advance(121)
    with pytest.raises(EgressRefused) as expired:
        _ = grants.admit(_request(late), _target(), _binding(), ACCESS)
    assert expired.value.reason is R.TOKEN_REPLAYED


def test_gitf23_a_grant_is_its_sandboxs_and_its_repositorys_and_never_a_fetch() -> None:
    grants = PushGrants(ManualClock())
    placeholder = _issue(grants)
    with pytest.raises(EgressRefused) as foreign:
        _ = grants.admit(_request(placeholder), _target(), _binding("sbx-2"), ACCESS)
    assert foreign.value.reason is R.PLACEHOLDER_FOREIGN
    with pytest.raises(EgressRefused) as other:
        _ = grants.admit(_request(placeholder), _target(), _binding(), GitAccess("acme/other"))
    assert other.value.reason is R.GIT_OPERATION_NOT_ALLOWED
    with pytest.raises(EgressRefused) as fetch:
        _ = grants.admit(_request(placeholder, path=f"/{REPO}.git/git-upload-pack"), _target(f"/{REPO}.git/git-upload-pack"), _binding(), ACCESS)
    assert fetch.value.reason is R.GIT_OPERATION_NOT_ALLOWED
    assert grants.admit(_request(new_placeholder()), _target(), _binding(), ACCESS) is None
    grants.drop("sbx-1")
    assert not grants.is_grant(placeholder)


@pytest.mark.parametrize(
    "change",
    [
        {"placeholder": "not-a-placeholder"},
        {"repository": "acme/other"},
        {"repository": "../etc"},
        {"branch": "--force"},
        {"credential_ref": "plain-token"},
        {"ttl_s": MAX_TTL_S + 1},
        {"ttl_s": 0},
    ],
)
def test_gitf23_a_malformed_grant_is_never_held(change: dict[str, object]) -> None:
    with pytest.raises(PushGrantInvalid):
        _ = _issue(PushGrants(ManualClock()), **change)


def test_gitf23_a_placeholder_is_granted_once_and_a_sandbox_holds_few() -> None:
    grants = PushGrants(ManualClock())
    placeholder = _issue(grants)
    with pytest.raises(PushGrantInvalid):
        _ = _issue(grants, placeholder=placeholder)
    for _ in range(7):
        _ = _issue(grants)
    with pytest.raises(PushGrantInvalid):
        _ = _issue(grants)
