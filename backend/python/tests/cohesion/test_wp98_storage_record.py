"""WP98-REC-1..2: the references record is one shape on both sides (ADR-RUN-01 D2).

The worker writes ``.pipeshub/references.json`` (``storage_classes.encode_record``); Node lists its entries as
references and never as files (``domain/storage-record.ts``). A path or bound that drifted would show the record as a
file, or lose the person's reference lines.
"""

from __future__ import annotations

import json
import re

from harness_core.workspace.sync.storage_classes import (
    MAX_RECORD_BYTES,
    MAX_RECORD_REFERENCES,
    RECORD_SCHEMA,
    STORAGE_RECORD_PATH,
    Reference,
    decode_record,
    encode_record,
)
from tests.cohesion.support import PYTHON_ROOT

NODE_RECORD = PYTHON_ROOT.parent / "nodejs/apps/src/modules/agent-harness/domain/storage-record.ts"
VECTORS = PYTHON_ROOT.parents[1] / "contracts/harness/vectors/storage-record.json"


def test_wp98_rec_1_node_reads_the_record_where_and_as_the_worker_writes_it() -> None:
    source = NODE_RECORD.read_text(encoding="utf-8")
    directory = re.search(r"export const STORAGE_RECORD_DIR = '([^']+)';", source)
    assert directory is not None and STORAGE_RECORD_PATH == f"{directory.group(1)}/references.json"
    assert "`${STORAGE_RECORD_DIR}/references.json`" in source
    for name, value in (
        ("STORAGE_RECORD_SCHEMA", RECORD_SCHEMA),
        ("MAX_RECORD_REFERENCES", MAX_RECORD_REFERENCES),
        ("MAX_RECORD_BYTES", MAX_RECORD_BYTES),
    ):
        found = re.search(rf"export const {name} = ([\d_ *]+);", source)
        assert found is not None, name
        assert eval(found.group(1).replace("_", "")) == value, name


def test_wp98_rec_2_the_shared_vectors_are_what_the_worker_writes() -> None:
    vectors = json.loads(VECTORS.read_text(encoding="utf-8"))
    assert vectors["path"] == STORAGE_RECORD_PATH
    references = decode_record(vectors["record"].encode())
    assert encode_record(references).decode() == vectors["record"]
    assert [(r.path, r.label, r.address, r.version) for r in references] == [
        (v["path"], v["label"], v["address"], v.get("version")) for v in vectors["references"]
    ]
    assert references[1] == Reference("repos/onyx", "https://github.com/onyx-dot-app/onyx", "57cb7b8e0f12a9", ref="main")
    for bad in vectors["unreadable"]:
        assert decode_record(bad.encode()) == ()
