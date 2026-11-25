"""R32-REPO: the e2e lanes' stand-in git host never reaches the human-test stack or a sandbox, and opens one name.

The repository journeys (deployment/e2e/journeys.py repo_*) clone from a host that answers as github.com on the stack's
own network (docker-compose.fakegit.yml). It must stay a lane fixture: people on the human-test stack read the real
github.com, so its script turns it off; no sandbox network may resolve the alias; and the worker opens exactly that
name, so the product's address checks hold for everything else.
"""

from __future__ import annotations

import re

import yaml

from tests.cohesion.support import REPO_ROOT

E2E = REPO_ROOT / "deployment/e2e"


class _Compose(yaml.SafeLoader):
    """Compose's own tags (``!reset``) read as plain values."""


def _plain(loader: yaml.SafeLoader, _tag: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


_Compose.add_multi_constructor("!", _plain)


def overlay() -> dict:
    return yaml.load((E2E / "docker-compose.fakegit.yml").read_text(), Loader=_Compose)


def test_r32repo_e2e_the_alias_lives_on_the_stacks_network_only() -> None:
    host = overlay()["services"]["pxh-fake-git"]
    assert host["networks"] == {"pipeshub": {"aliases": ["github.com"]}}
    assert "ports" not in host


def test_r32repo_e2e_the_worker_opens_that_one_name_and_trusts_the_test_authority() -> None:
    env = overlay()["services"]["pipeshub-ai"]["environment"]
    assert "EGRESS_PRIVATE_ALLOW=github.com:443" in env
    assert "HARNESS_REPO_CA_FILE=/git-ca/ca.pem" in env
    assert set(overlay()["services"]) == {"pxh-fake-git", "pipeshub-ai"}


def test_r32repo_e2e_human_stack_never_gets_it_and_human_up_lanes_do() -> None:
    script = (REPO_ROOT / "scripts/human-test-stack.sh").read_text()
    assert re.search(r"^export E2E_FAKE_GIT=0$", script, re.MULTILINE)
    run = (E2E / "run.sh").read_text()
    assert 'export E2E_FAKE_GIT="${E2E_FAKE_GIT:-1}"' in run
    assert '[[ "$(envv E2E_FAKE_GIT)" == "1" ]] && files+=(-f "$FAKEGIT_OVERLAY")' in run
