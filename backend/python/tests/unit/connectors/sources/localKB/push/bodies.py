"""Request bodies for the push tests."""

import json

TEXT = {"index": 0, "type": "text", "format": "txt", "data": "hello"}


def envelope(blocks: object = None, **over: object) -> dict:
    body = {
        "recordName": "r",
        "mimeType": "application/blocks",
        "externalRecordId": "k1",
        "version": 1,
        "blocksFormat": 1,
        "blocks": {"blocks": [TEXT]} if blocks is None else blocks,
    }
    body.update(over)
    return {k: v for k, v in body.items() if v is not _OMIT}


_OMIT = object()
OMIT = _OMIT


def raw(obj: object) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode()


def schema_names(n: int = 256) -> frozenset:
    return frozenset(f"p{i:03d}" for i in range(n))


def cells_body(rows: int, cells: int = 512) -> bytes:
    row = ('{"index":%d,"type":"table_row","format":"json","parent_index":0,"data":{"cells":["%d"' + ',""' * (cells - 1) + "]}}")
    blocks = ",".join(row % (i, i) for i in range(rows))
    return (
        '{"recordName":"r","mimeType":"application/blocks","externalRecordId":"k","version":1,"blocksFormat":1,"blocks":{'
        '"block_groups":[{"index":0,"type":"table","table_metadata":{"num_of_cells":1},'
        f'"children":{{"block_ranges":[{{"start":0,"end":{rows - 1}}}]}}}}],"blocks":[{blocks}]}}}}'
    ).encode()
