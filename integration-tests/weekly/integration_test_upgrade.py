"""Upgrade smoke: seed on the previous release, switch to this commit, check nothing was lost.

Runs only on the engine's `upgrade` stack variant: the tests marked `upgrade_before` run on the
previous release, then the app is recreated on this commit's image over the same volumes and
booted twice, then the rest run. No model is used.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest

from helper import realistic_files
from helper.clients.kb_client import KBClient
from helper.clients.user_groups_client import UserGroupsClient
from helper.clients.users_client import UsersClient
from helper.pipeshub_users import ensure_pipeshub_user_exists
from helper.upgrade_snapshot import diff
from weekly.conftest import snapshot

pytestmark = pytest.mark.upgrade


def _dir() -> Path:
    path = os.environ.get("QA_UPGRADE_DIR")
    if not path:
        pytest.fail("QA_UPGRADE_DIR is not set: run this on the upgrade stack variant (qa run --stack-variant upgrade)")
    return Path(path)


def _read(name: str) -> dict:
    path = _dir() / name
    if not path.is_file():
        pytest.fail(f"{path} is missing: the seeding test did not run on the previous release")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.order(1)
@pytest.mark.upgrade_before
def test_seed_before_upgrade(pipeshub_client) -> None:
    """Seed a KB with three files, a group and a member, on the previous release."""
    token = uuid.uuid4().hex[:8]
    kb_client = KBClient(pipeshub_client)
    kb_id = kb_client.create_kb(f"upgrade-{token}")["id"]
    record_ids = []
    for f in (realistic_files.unicode_markdown(), realistic_files.messy_csv(), realistic_files.multi_page_docx()):
        upload = kb_client.upload_file(kb_id, f.name, f.body, mimetype=f.mimetype, file_path=f.upload_path)
        assert upload["summary"]["failed"] == 0, f"upload of {f.name} failed: {upload}"
        record_ids.append(upload["records"][0]["recordId"])
    user_id, _ = ensure_pipeshub_user_exists(UsersClient(pipeshub_client), f"upgrade-{token}@example.com")
    groups = UserGroupsClient(pipeshub_client)
    created = groups.create_group(f"upgrade-{token}")
    assert created.status_code in (200, 201), created.text
    group_id = created.json().get("_id") or created.json().get("id")
    added = groups.add_users([user_id], [group_id])
    assert added.status_code in (200, 201), added.text
    out = _dir()
    out.mkdir(parents=True, exist_ok=True)
    (out / "before.json").write_text(json.dumps(snapshot(pipeshub_client, kb_id)), encoding="utf-8")
    (out / "ids.json").write_text(json.dumps({"kb_id": kb_id, "record_ids": record_ids, "user_id": user_id, "group_id": group_id}), encoding="utf-8")


@pytest.mark.order(2)
def test_counts_survive_the_upgrade(pipeshub_client) -> None:
    """Every count taken before the upgrade is still there after two boots of this commit."""
    before, ids = _read("before.json"), _read("ids.json")
    assert diff(before, snapshot(pipeshub_client, ids["kb_id"])) == []


@pytest.mark.order(3)
def test_seeded_items_still_open(pipeshub_client) -> None:
    """The seeded KB, its records, the group and the user each answer 200."""
    ids = _read("ids.json")
    kb = KBClient(pipeshub_client)
    paths = [kb.get(f"/{ids['kb_id']}")]
    paths += [kb.get(f"/record/{rid}") for rid in ids["record_ids"]]
    paths.append(UserGroupsClient(pipeshub_client).get_group(ids["group_id"]))
    paths.append(UsersClient(pipeshub_client).get_user(ids["user_id"]))
    bad = [f"{r.request.method} {r.request.url}: {r.status_code}" for r in paths if r.status_code != 200]
    assert not bad, bad
