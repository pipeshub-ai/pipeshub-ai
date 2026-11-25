"""C4.10: every row of checklist §4.2 says what carries it (``contracts/harness/config-surface.json``).

A row carried by dimensions names rows of the dimension table, which QA-2 (precedence) and C4.02 (locks) hold for
every row; a row carried by operator env names flags.json entries; a row carried by a layer section names a member
of the policy layer. A partial or missing row names the package that adds the rest. A new §4.2 row, or a renamed
dimension or flag, fails here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from tests.cohesion.support import (
    REPO_ROOT,
    contract_schema,
    policy_dimensions,
    with_overlay,
)

CONTRACTS: Final = REPO_ROOT / "contracts" / "harness"
BASE_SURFACE: list[dict[str, Any]] = json.loads((CONTRACTS / "config-surface.json").read_text(encoding="utf-8"))["rows"]
# With an edition's overlay rows (``contracts/harness/ee/config-surface.json``), when present: every row's carriers
# exist; the checklist's order holds for this repository's rows.
SURFACE: list[dict[str, Any]] = with_overlay({"rows": BASE_SURFACE}, "config-surface.json")["rows"]
DIMENSIONS: dict[str, dict[str, Any]] = {row["path"]: row for row in policy_dimensions()}
FLAGS: Final = with_overlay(json.loads((CONTRACTS / "flags.json").read_text(encoding="utf-8")), "flags.json")
FLAG_NAMES: Final = {f["name"] for f in FLAGS["flags"]} | {s["name"] for s in FLAGS["settings"]}
LAYER_SECTIONS: Final = set(contract_schema(CONTRACTS / "core" / "policy" / "policy-layer.schema.json")["properties"])
CHECKLIST: Final = REPO_ROOT / "docs" / "agent-harness-v2" / "checklist.md"
CARRIERS: Final = {"dimension": "dimensions", "flag": "flags", "section": "sections"}


def _checklist_rows() -> list[tuple[str, bool]]:
    """§4.2's rows: the setting and whether an org admin can lock it."""
    text = CHECKLIST.read_text(encoding="utf-8")
    table = text[text.index("### 4.2") : text.index("All defaults above")]
    rows: list[tuple[str, bool]] = []
    for line in table.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.startswith("| ") or cells[0] == "Setting" or re.fullmatch(r"-+", cells[0]):
            continue
        rows.append((cells[0], cells[7] == "●"))
    return rows


def test_c4_10_every_checklist_row_is_in_the_surface_once_in_order() -> None:
    assert [row["setting"] for row in BASE_SURFACE] == [setting for setting, _ in _checklist_rows()]


def test_c4_10_each_row_names_carriers_that_exist() -> None:
    for row in SURFACE:
        assert row["status"] in (*CARRIERS, "partial", "missing"), row["setting"]
        assert set(row.get("dimensions", [])) <= set(DIMENSIONS), row["setting"]
        assert set(row.get("flags", [])) <= FLAG_NAMES, row["setting"]
        assert set(row.get("sections", [])) <= LAYER_SECTIONS - set(DIMENSIONS), row["setting"]
        if row["status"] in CARRIERS:
            assert row.get(CARRIERS[row["status"]]), row["setting"]
            assert "next" not in row, row["setting"]
        else:
            assert row.get("next"), f"{row['setting']}: a partial or missing row names the package that adds it"
        if row["status"] == "missing":
            assert not any(key in row for key in CARRIERS.values()), row["setting"]


def test_c4_10_a_lockable_row_carried_by_policy_has_precedence_and_lock_tests() -> None:
    """Each dimension of a row an admin can lock has a QA-2 ladder or choice sample, which C4.02's LK-1 and LK-2 lock
    at its top layer and try to override from every layer below (the admitted engine by the PR vectors); an
    operator row is the deployment's alone, which no layer below may set."""
    stricter = json.loads((CONTRACTS / "vectors" / "stricter-only.json").read_text(encoding="utf-8"))
    sampled = {ladder["path"] for ladder in stricter["ladders"]} | {choice["path"] for choice in stricter["choices"]}
    lockable = dict(_checklist_rows())
    for row in BASE_SURFACE:
        if not lockable[row["setting"]]:
            continue
        for path in row.get("dimensions", []):
            assert DIMENSIONS[path]["kind"] == "operator" or path in sampled, path


def test_c4_10_the_new_rows_this_package_added_are_dimensions() -> None:
    by_setting = {row["setting"]: row for row in SURFACE}
    assert by_setting["Tool disclosure (`eager` / `lazy` / `filesystem`)"]["dimensions"] == [
        "toolDisclosure.modes",
        "toolDisclosure.mode",
    ]


def test_c4_10_wp23_the_snapshot_ttl_is_a_dimension_with_the_release_default_of_7_days() -> None:
    """OWD-12 (WP-23): a 7-day default, shortened by the deployment or the org, locked like every ceiling."""
    by_setting = {row["setting"]: row for row in SURFACE}
    assert by_setting["Snapshot TTL / retention"]["dimensions"] == ["snapshots.ttlDays"]
    release = json.loads((CONTRACTS / "core" / "policy" / "release.json").read_text(encoding="utf-8"))
    assert release["snapshots"] == {"ttlDays": 7}
    assert DIMENSIONS["snapshots.ttlDays"]["kind"] == "ceiling"
    assert DIMENSIONS["snapshots.ttlDays"]["order"] == {"kind": "min"}
    assert DIMENSIONS["snapshots.ttlDays"]["exposure"] == ["deployment", "org"]


def test_c4_10_the_session_retention_default_is_no_expiry() -> None:
    """No retention configured means nothing expires; the surface documents it."""
    row = {row["setting"]: row for row in SURFACE}["Transcript + audit retention"]
    assert row["default"] is None
    assert "no expiry" in row["note"]
    assert "configurable per org" in row["note"]


WP68_DIMENSIONS: Final = {
    "quotas.activeRunsPerOrg": 200,
    "quotas.activeRunsPerUser": 10,
    "toolResults.maxChars": None,
    "interactions.waitS": None,
    "interactions.conversationGrants": True,
    "recovery.autoResume": True,
    "sandbox.cpus": None,
    "sandbox.memoryMb": None,
    "sandbox.pids": None,
    "sandbox.scratchMb": None,
    "ui.reasoning": True,
    "ui.terminal": True,
}


def test_c4_10_wp68_every_row_is_carried_and_none_is_partial_or_missing() -> None:
    """C410-1 (WP-68): every §4.2 row names what carries it; none waits on a package."""
    assert [row["setting"] for row in SURFACE if row["status"] in ("partial", "missing")] == []


def test_c4_10_wp68_the_new_dimensions_have_their_release_defaults_and_ladders() -> None:
    """C410-2 (WP-68): each new ceiling has its release default (null: the executor's or the template's own) and
    a QA-2 ladder, which LK-1/LK-2 lock and override from every lower layer; the response_format default is a
    choice inside its ceiling with a QA-2 sample."""
    release = json.loads((CONTRACTS / "core" / "policy" / "release.json").read_text(encoding="utf-8"))
    stricter = json.loads((CONTRACTS / "vectors" / "stricter-only.json").read_text(encoding="utf-8"))
    ladders = {ladder["path"] for ladder in stricter["ladders"]}
    for path, default in WP68_DIMENSIONS.items():
        section, field = path.split(".")
        assert release[section][field] == default, path
        assert DIMENSIONS[path]["kind"] == "ceiling", path
        assert path in ladders, path
    assert DIMENSIONS["toolResults.format"] == {
        **DIMENSIONS["toolResults.format"],
        "kind": "choice",
        "within": "toolResults.formats",
    }
    assert "toolResults.format" in {choice["path"] for choice in stricter["choices"]}


def test_c4_10_wp68_each_new_dimension_is_documented() -> None:
    """C410-3 (WP-68): the admin guide names every dimension the surface adds, with its default."""
    guide = (REPO_ROOT / "docs" / "agent-harness-v2" / "guides" / "builder-guide.md").read_text(encoding="utf-8")
    for path in (*WP68_DIMENSIONS, "toolResults.formats", "toolResults.format"):
        assert f"`{path}`" in guide, path


def test_j26_8_the_input_cap_is_the_deployments_setting_an_org_may_only_lower() -> None:
    """J26-8 (WP-69): ``sandbox.inputMaxMb`` is a ceiling only the deployment and the org set, with no release value
    (the deployment's ``HARNESS_INPUT_MAX_MB`` applies) and a QA-2 ladder; the surface names both."""
    release = json.loads((CONTRACTS / "core" / "policy" / "release.json").read_text(encoding="utf-8"))
    stricter = json.loads((CONTRACTS / "vectors" / "stricter-only.json").read_text(encoding="utf-8"))
    assert release["sandbox"]["inputMaxMb"] is None
    row = DIMENSIONS["sandbox.inputMaxMb"]
    assert (row["kind"], row["order"], row["exposure"]) == ("ceiling", {"kind": "min"}, ["deployment", "org"])
    assert "sandbox.inputMaxMb" in {ladder["path"] for ladder in stricter["ladders"]}
    surface = {row["setting"]: row for row in SURFACE}["Sandbox CPU / memory / disk / pids"]
    assert "sandbox.inputMaxMb" in surface["dimensions"]
    assert "HARNESS_INPUT_MAX_MB" in surface["flags"]
    assert "HARNESS_INPUT_MAX_MB" in FLAG_NAMES
