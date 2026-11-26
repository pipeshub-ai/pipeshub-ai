"""WP98-REG-1..7: the storage class registry's contract (ADR-RUN-01 D2).

One registry says which class a path is (durable, reference, scratch); producers extend it by declaring, and the sync
asks it and keeps no skip lists of its own (a skip list in the sync was WP-82's patch for the clones).
"""

from __future__ import annotations

import inspect

import pytest

from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.durable.handoff import (
    FETCHED_DIR,
    HANDOFF_PATH,
    OUTPUTS_DIR,
    fetched_path,
)
from harness_core.gateway.result_cap import cap_engine_result
from harness_core.gateway.types import McpResult
from harness_core.workspace.sync import storage_classes, tar_sync
from harness_core.workspace.sync.storage_classes import (
    DELIVERABLES_DIR,
    DURABLE,
    ENGINE_TOOL_OUTPUT,
    MAX_RECORD_BYTES,
    SCRATCH,
    TOOL_RESULTS_DIR,
    PathRule,
    Reference,
    StorageClassRegistry,
    checkout_reference,
    decode_record,
    encode_record,
    merged_references,
    reference_identity,
)

PH_CONFIG = (
    b"[core]\n\tbare = false\n"
    b'[remote "origin"]\n\turl = https://github.com/onyx-dot-app/onyx\n'
    b"[pipeshub]\n\tfetched = true\n\tcommit = 57cb7b8e0f\n\tref = main\n"
)
ONYX = Reference("repos/onyx", "https://github.com/onyx-dot-app/onyx", "57cb7b8e0f", ref="main")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("out/report.md", DURABLE),
        ("notes/summary.md", DURABLE),
        ("notes/tool-results/search-1.json", SCRATCH),
        ("notes/tool-results/fetch_url-2.txt", SCRATCH),
        # WP98-FU-4: a dump anywhere else under notes/ is durable, as a person's own notes/plan.json is.
        ("notes/web/onyx-docs.json", DURABLE),
        ("notes/plan.json", DURABLE),
        ("notes/web/pages.jsonl", DURABLE),
        (HANDOFF_PATH, DURABLE),
        (fetched_path("lookup", "sha256:" + "a" * 64), DURABLE),
        ("data/results.json", DURABLE),
        ("notes.json", DURABLE),
    ],
)
def test_wp98_reg_1_each_path_has_one_class(path: str, expected: str) -> None:
    assert StorageClassRegistry.default().declared(path) == expected


def test_wp98_reg_2_ph_git_clone_registers_its_checkout_by_its_mark() -> None:
    assert checkout_reference("repos/onyx/.git/config", PH_CONFIG) == ONYX
    # A checkout from before the commit was recorded (WP-63) is still a reference, without a version.
    old = b'[remote "origin"]\n\turl = https://github.com/acme/app\n[pipeshub]\n\tfetched = true\n'
    assert checkout_reference("app/.git/config", old) == Reference("app", "https://github.com/acme/app")
    own = b'[remote "origin"]\n\turl = https://github.com/acme/app\n'
    assert checkout_reference("app/.git/config", own) is None
    assert checkout_reference("app/.git/config", b"[pipeshub]\n\tfetched = true\n") is None
    assert checkout_reference(".git/config", PH_CONFIG) is None
    assert ONYX.label == "onyx-dot-app/onyx"
    assert ONYX.source == "https://github.com/onyx-dot-app/onyx@57cb7b8e0f"
    assert reference_identity("https://GitHub.com/Onyx-Dot-App/onyx.git/") == ONYX.identity


def test_wp98_reg_3_the_record_holds_addresses_and_versions_only() -> None:
    record = encode_record([ONYX])
    assert decode_record(record) == (ONYX,)
    assert b"57cb7b8e0f" in record and len(record) < 400
    assert decode_record(b"not json") == ()
    assert decode_record(b'{"schema": 2, "references": []}') == ()
    assert decode_record(b'{"schema": 1, "references": [{"path": "x", "address": "http://insecure"}]}') == ()
    assert decode_record(b" " * (MAX_RECORD_BYTES + 1)) == ()


def test_wp98_reg_4_the_store_s_files_stay_the_person_s_and_a_cited_dump_is_kept() -> None:
    registry = StorageClassRegistry.default()
    paths = [
        "notes/tool-results/a-1.json", "notes/tool-results/b-1.json", "notes/tool-results/old-1.json",
        "repos/onyx/README.md", "out/report.md",
    ]
    found = registry.storage_map(
        paths,
        [ONYX],
        durable=["notes/tool-results/old-1.json"],
        citing=["# Report\nSee notes/tool-results/b-1.json for the raw page."],
    )
    assert found.scratch == frozenset({"notes/tool-results/a-1.json"})
    assert found.reference_folders == frozenset({"repos/onyx"})


def test_wp98_reg_5_a_later_scan_moves_a_reference_s_version_and_keeps_the_others() -> None:
    moved = Reference("repos/onyx", ONYX.address, "9999999", ref="main")
    other = Reference("repos/docs", "https://github.com/onyx-dot-app/docs", "1234567")
    assert merged_references([ONYX, other], [moved]) == (other, moved)


def test_wp98_reg_6_the_declarations_name_the_producers_own_paths() -> None:
    # The handoff's files are the person's: a later "continue" reads them; ph's folder is neither of them.
    assert ENGINE_TOOL_OUTPUT.folder == TOOL_RESULTS_DIR
    assert not HANDOFF_PATH.startswith(f"{TOOL_RESULTS_DIR}/") and not FETCHED_DIR.startswith(TOOL_RESULTS_DIR)
    assert DELIVERABLES_DIR == OUTPUTS_DIR == EngineControllerConfig(images={}).outputs_dir
    # The result cap's own suggestion for a whole result names ph's folder, which is scratch.
    capped = cap_engine_result(
        McpResult("x" * 50, is_error=False, disposition="executed", retryable=False),
        tool="github",
        arguments={},
        max_chars=10,
    )
    assert f"{TOOL_RESULTS_DIR}/" in capped.text and "> notes/" not in capped.text
    assert StorageClassRegistry.default().declared(f"{TOOL_RESULTS_DIR}/github-1.json") == SCRATCH


def test_wp98_reg_7_a_producer_extends_the_registry_and_the_sync_keeps_no_list_of_its_own() -> None:
    registry = StorageClassRegistry.default().declare(PathRule("a build tool", SCRATCH, "build"))
    assert registry.declared("build/app.o") == SCRATCH
    assert StorageClassRegistry.default().declared("build/app.o") == DURABLE
    source = inspect.getsource(tar_sync)
    for gone in ("fetched_checkouts", "marks_fetched", "FETCHED_REPOSITORY", "notes/"):
        assert gone not in source
    assert "StorageClassRegistry" in inspect.getsource(storage_classes)
