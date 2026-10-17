"""The parsing service keeps one parser instance per format and serves many
files at once, so a parser must never mix up two files parsed together."""

from __future__ import annotations

import asyncio
import json

import yaml

from app.modules.parsers.json.json_parser import JSONParser
from app.modules.parsers.yaml.yaml_parser import YAMLParser

from .samples import all_text


def _document(tag: str) -> dict:
    return {
        "owner": tag,
        "items": [{"id": i, "who": tag, "note": f"{tag}-note-{i}"} for i in range(300)],
        "meta": {"region": {"name": f"{tag}-region", "code": tag}},
    }


async def test_concurrent_json_and_yaml_files_do_not_mix() -> None:
    shared = JSONParser()
    yaml_parser = YAMLParser(shared)
    tags = [f"tenant{i}" for i in range(8)]
    solo_counts = {
        tag: len((await JSONParser().parse(json.dumps(_document(tag)).encode(), f"{tag}.json")).block_container.blocks)
        for tag in tags
    }

    for _ in range(3):
        calls = []
        for i, tag in enumerate(tags):
            if i % 2:
                calls.append(yaml_parser.parse(yaml.safe_dump(_document(tag)).encode(), f"{tag}.yaml"))
            else:
                calls.append(shared.parse(json.dumps(_document(tag)).encode(), f"{tag}.json"))
        results = await asyncio.gather(*calls)

        for tag, result in zip(tags, results):
            text = all_text(result.block_container)
            leaked = [other for other in tags if other != tag and f"{other}-note-" in text]
            assert leaked == [], f"{tag} contains content from {leaked}"
            assert f"{tag}-note-299" in text
            assert len(result.block_container.blocks) == solo_counts[tag]
