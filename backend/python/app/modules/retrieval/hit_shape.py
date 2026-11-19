"""The one shape of a search hit, and the one place a hit stops being internal.

Inside the query service a hit carries two ids when the content behind a record is held in a stored record of its own:

* ``virtualRecordId`` / ``identity_vrid``  the record a user, an access rule and a citation are about (the key of the
  record maps);
* ``contentVrid`` / ``virtual_record_id`` of a flattened passage  the stored record that holds the passage: the only key
  a loader reads content by;
* ``holder_index``  which holder of the record's content the passage came from, and ``block_index`` the passage's index
  inside that holder.

With the default locator the two ids are equal and there is one holder, so every function here is the identity.
``public_hit`` and ``public_results`` are the serialisation boundary: they drop what is internal and write the
index a client sees (``locator.public_index``) where the local one was. Nothing else in the query side branches on it.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.retrieval.content_locator import ContentLocator

# Point-payload fields the process stamps on a hit that never leave it (a locator adds its own).
INTERNAL_PAYLOAD_KEYS = ("contentVrid", "blockAddress", "holderIndex")
# Keys a flattened hit (or one of its children) carries only inside the process.
INTERNAL_HIT_KEYS = ("identity_vrid", "holder_index")


def content_vrid(hit: Mapping[str, Any]) -> str | None:
    """The stored record holding a hit's content: the only way a loader picks which record to read.

    Takes a point's metadata (``contentVrid``), a hit from the vector search (``{"metadata": ..}``) or a
    flattened passage, whose ``virtual_record_id`` is already the stored record it was read from.
    """
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), Mapping) else {}
    return (
        hit.get("contentVrid") or metadata.get("contentVrid")
        or hit.get("virtual_record_id") or hit.get("virtualRecordId") or metadata.get("virtualRecordId")
    )


def identity_vrid(hit: Mapping[str, Any]) -> str | None:
    """The record a hit is about (the key of ``virtual_to_record_map``), whatever stored record holds its text."""
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), Mapping) else {}
    return metadata.get("virtualRecordId") or hit.get("identity_vrid") or hit.get("virtual_record_id")


def holder_index_of(hit: Mapping[str, Any]) -> int | None:
    """Which holder of the record's content a hit's passage came from (``None``: the record's own)."""
    metadata = hit.get("metadata") if isinstance(hit.get("metadata"), Mapping) else {}
    found = hit.get("holder_index") or hit.get("holderIndex") or metadata.get("holderIndex")
    return int(found) if found else None


def carry_holder(source: Mapping[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Give a passage made from the same stored record as ``source`` (a neighbour) the same internal marks."""
    for key in INTERNAL_HIT_KEYS:
        if source.get(key) is not None:
            target[key] = source[key]
    return target


def _internal_payload_keys(locator: ContentLocator | None) -> frozenset[str]:
    return frozenset((*INTERNAL_PAYLOAD_KEYS, *(getattr(locator, "internal_payload_keys", None) or ())))


def _public_children(content: object, locator: ContentLocator | None) -> object:
    # A group's content is ``(summary, children)``; JSON turns it into a list.
    if isinstance(content, (tuple, list)) and len(content) == 2 and isinstance(content[1], list):
        return type(content)((content[0], [
            public_hit(child, locator) if isinstance(child, dict) else child for child in content[1]
        ]))
    return content


def public_hit(hit: Mapping[str, Any], locator: ContentLocator | None = None) -> dict[str, Any]:
    """A copy of a flattened hit safe to send: the client's index for the local one, no internal marks."""
    out = dict(hit)
    internal = _internal_payload_keys(locator)
    if isinstance(out.get("metadata"), Mapping) and out["metadata"].get("blockAddress") is not None:
        # A hit that was not flattened: its metadata still holds the local index.
        out["metadata"] = {**out["metadata"], "blockIndex": out["metadata"]["blockAddress"]}
    if locator is not None and "block_index" in out and not isinstance(out["block_index"], bool) and out["block_index"] is not None:
        out["block_index"] = locator.public_index(hit.get("holder_index"), out["block_index"])
    metadata = out.get("metadata")
    if isinstance(metadata, Mapping):
        out["metadata"] = {k: v for k, v in metadata.items() if k not in internal}
    if "content" in out:
        out["content"] = _public_children(out["content"], locator)
    if out.get("identity_vrid"):
        out["virtual_record_id"] = out["identity_vrid"]
    for key in INTERNAL_HIT_KEYS:
        out.pop(key, None)
    return out


def public_hits(hits: Iterable[Any], locator: ContentLocator | None = None) -> list[Any]:
    return [public_hit(h, locator) if isinstance(h, dict) else h for h in hits]


_RECORD_KEYED = frozenset({"records", "virtual_to_record_map"})


def _is_hit(value: object) -> bool:
    return isinstance(value, dict) and (isinstance(value.get("metadata"), Mapping) or any(k in value for k in INTERNAL_HIT_KEYS))


def _public(value: Any, locator: ContentLocator | None) -> Any:  # noqa: ANN401
    if isinstance(value, list):
        return [public_hit(v, locator) if _is_hit(v) else _public(v, locator) for v in value]
    if isinstance(value, dict):
        return {k: (v if k in _RECORD_KEYED else _public(v, locator)) for k, v in value.items()}
    return value


def public_results(results: Mapping[str, Any], locator: ContentLocator | None = None) -> dict[str, Any]:
    """A search response safe to send: every passage in it, wherever the response holds one (``searchResults``,
    ``outsideDates.searchResults``, any collection a later path adds), goes through ``public_hit``: the one boundary
    where internal marks and local indices are removed. ``records`` and ``virtual_to_record_map`` describe records
    and are keyed by them, so they are sent as they are."""
    return _public(dict(results), locator)
