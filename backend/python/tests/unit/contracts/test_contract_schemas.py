"""Validate the repo-root ``contracts/`` example payloads (PH02-23)."""

import json
from pathlib import Path

import jsonschema
import pytest

CONTRACTS_DIR = Path(__file__).resolve().parents[5] / "contracts"


def _cases() -> list[tuple[str, str, str, int, object]]:
    cases = []
    for folder in sorted(p for p in CONTRACTS_DIR.iterdir() if p.is_dir()):
        examples = json.loads((folder / "examples.json").read_text())
        for direction, groups in examples.items():
            for kind in ("valid", "invalid"):
                for i, payload in enumerate(groups[kind]):
                    cases.append((folder.name, direction, kind, i, payload))
    return cases


def _schema(name: str, direction: str) -> dict:
    return json.loads((CONTRACTS_DIR / name / f"{direction}.schema.json").read_text())


def test_attachments_validate_contract_is_present() -> None:
    assert (CONTRACTS_DIR / "attachments-validate" / "request.schema.json").is_file()


@pytest.mark.parametrize(
    ("name", "direction", "kind", "index", "payload"),
    _cases(),
    ids=lambda v: v if isinstance(v, (str, int)) else "payload",
)
def test_example_matches_schema(name, direction, kind, index, payload) -> None:
    schema = _schema(name, direction)
    jsonschema.Draft7Validator.check_schema(schema)
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    if kind == "valid":
        assert not errors, [e.message for e in errors]
    else:
        assert errors, f"{name}/{direction} invalid example {index} unexpectedly validated"
