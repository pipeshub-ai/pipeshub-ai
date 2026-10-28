"""Loads the Acme Corp fixture: ``acme-corp.yaml`` plus ``acme-corp.d/*.yaml``.

The main file keeps the golden questions and every original record; the
extension files add people, organisations, groups, containers, threads and
records, merged in file-name order. A duplicate id or a reference to
something the fixture does not have fails loading, naming the item, so a
typo never becomes a record synced without its author.

Imports nothing from the app beyond ``systems``: the knowledge-base harness
runs it as a plain script.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

try:
    from app.connectors.sources.demo.systems import PERSON_FIELDS, SYSTEMS, shape_of
except ImportError:  # run as a script by the harness, with this folder on the path
    from systems import PERSON_FIELDS, SYSTEMS, shape_of  # type: ignore[no-redef]

FIXTURE_PATH = Path(__file__).parent / "fixture" / "acme-corp.yaml"
MERGED_LISTS = ("people", "organizations", "groups", "containers", "threads", "records")
_PERSON_KINDS = frozenset({"member", "external"})


class FixtureError(ValueError):
    """The fixture is inconsistent; the message names the file or item."""


def load_fixture(path: Path = FIXTURE_PATH) -> dict[str, Any]:
    """The main fixture with its extension directory merged in, validated."""
    fx = _read(path)
    for name in MERGED_LISTS:
        fx[name] = list(fx.get(name) or [])
    for extension in sorted(path.with_suffix(".d").glob("*.yaml")):
        _merge(fx, _read(extension), extension.name)
    validate_fixture(fx)
    return fx


def _read(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise FixtureError(f"{path.name}: expected a mapping at the top level")
    return data


def _merge(fx: dict[str, Any], extra: Mapping[str, Any], source: str) -> None:
    unknown = sorted(set(extra) - set(MERGED_LISTS))
    if unknown:
        raise FixtureError(f"{source}: only {', '.join(MERGED_LISTS)} merge; found {', '.join(unknown)}")
    for name in MERGED_LISTS:
        fx[name].extend(extra.get(name) or [])


def split_threads(fx: Mapping[str, Any]) -> tuple[list[dict], dict[str, list[dict]]]:
    """Records that stand alone, and each thread's messages oldest first.

    A thread is one record downstream, the way the Slack connector groups a
    burst of messages."""
    threads = {t["id"] for t in fx.get("threads", [])}
    singles: list[dict] = []
    by_thread: dict[str, list[dict]] = defaultdict(list)
    for rec in fx["records"]:
        if rec.get("thread") in threads:
            by_thread[rec["thread"]].append(rec)
        else:
            singles.append(rec)
    for messages in by_thread.values():
        messages.sort(key=lambda m: str(m["created"]))
    return singles, dict(by_thread)


def validate_fixture(fx: Mapping[str, Any]) -> None:
    _check_unique(fx)
    ids = {name: {item["id"] for item in fx.get(name, [])} for name in MERGED_LISTS}
    orgs = {o["id"]: o for o in fx.get("organizations", [])}
    containers = {c["id"]: c for c in fx.get("containers", [])}
    for person in fx.get("people", []):
        _check_person(person, ids, orgs)
    for container in fx.get("containers", []):
        _check_container(container, ids, orgs)
    for thread in fx.get("threads", []):
        _check_refs(f"thread {thread['id']}", "container", [thread.get("container")], ids["containers"])
    for rec in fx.get("records", []):
        _check_record(rec, ids, containers)


def _check_unique(fx: Mapping[str, Any]) -> None:
    for name in MERGED_LISTS:
        _first_duplicate(name, (item["id"] for item in fx.get(name, [])))
    # A thread is stored under its own id, next to the records.
    _first_duplicate("records", (item["id"] for name in ("records", "threads") for item in fx.get(name, [])))


def _first_duplicate(name: str, values: Iterable[str]) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise FixtureError(f"duplicate {name} id {value!r}")
        seen.add(value)


def _check_refs(owner: str, field: str, values: Iterable[Any], known: set[str]) -> None:
    for value in values:
        if value is not None and value not in known:
            raise FixtureError(f"{owner}: {field} names unknown {value!r}")


def _check_person(person: Mapping, ids: Mapping[str, set[str]], orgs: Mapping[str, Mapping]) -> None:
    owner = f"person {person['id']}"
    _check_refs(owner, "groups", person.get("groups", []), ids["groups"])
    kind = person.get("kind", "member")
    if kind not in _PERSON_KINDS:
        raise FixtureError(f"{owner}: kind must be one of {sorted(_PERSON_KINDS)}, not {kind!r}")
    if kind == "member":
        if person.get("company"):
            raise FixtureError(f"{owner}: company is for external people only")
        return
    company = person.get("company")
    _check_refs(owner, "company", [company], ids["organizations"])
    if not company:
        raise FixtureError(f"{owner}: an external person needs a company")
    domain = orgs[company].get("domain")
    if domain and not str(person.get("email", "")).lower().endswith("@" + domain.lower()):
        raise FixtureError(f"{owner}: an external person's address must be on {domain}")


def _check_container(container: Mapping, ids: Mapping[str, set[str]], orgs: Mapping[str, Mapping]) -> None:
    owner = f"container {container['id']}"
    spec = SYSTEMS.get(container.get("system"))
    if spec is None:
        raise FixtureError(f"{owner}: unknown system {container.get('system')!r}")
    if container.get("kind") not in spec.kinds:
        raise FixtureError(f"{owner}: a {container['system']} container is a {'/'.join(sorted(spec.kinds))}")
    _check_refs(owner, "group", [container.get("group")], ids["groups"])
    _check_refs(owner, "owner", [container.get("owner")], ids["people"])
    _check_refs(owner, "organization", [container.get("organization")], ids["organizations"])
    if container["system"] == "SALESFORCE" and not orgs.get(container.get("organization"), {}).get("crm"):
        raise FixtureError(f"{owner}: a Salesforce account names an organization with crm: true")


def _check_record(rec: Mapping, ids: Mapping[str, set[str]], containers: Mapping[str, Mapping]) -> None:
    owner = f"record {rec['id']}"
    _check_refs(owner, "container", [rec.get("container")], ids["containers"])
    _check_refs(owner, "group", [rec.get("group")], ids["groups"])
    _check_refs(owner, "thread", [rec.get("thread")], ids["threads"])
    _check_refs(owner, "mentions_orgs", rec.get("mentions_orgs", []), ids["organizations"])
    named = sorted(PERSON_FIELDS & set(rec))
    for field in named:
        value = rec[field]
        _check_refs(owner, field, value if isinstance(value, list) else [value], ids["people"])
    system = containers[rec["container"]]["system"]
    shape = shape_of(system, rec.get("type", ""))
    if shape is None:
        raise FixtureError(f"{owner}: the {system} connector emits no {rec.get('type')} records")
    extra = sorted(set(named) - shape.fields)
    if extra:
        raise FixtureError(
            f"{owner}: the {system} connector names no {', '.join(extra)} on a {rec['type']}"
        )
