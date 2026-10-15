"""Pick the integration-test shards and connector suites a pull request needs.

Reads the dorny/paths-filter outputs for .github/integration-test-paths.yml
(FILTER_OUTPUTS, the step's outputs as JSON, run with `list-files: json`) and
the CONN_SHARD_<n> lists from the environment. Writes to GITHUB_OUTPUT:

  shards   JSON list for the test matrix; "[]" means nothing to run.
  markers  space-separated connector markers to keep inside the connector
           shards; empty means keep every marker (a full run).

Run everything when any changed file is outside every filter (shared code), or
a file was renamed. Otherwise a connector shard runs only the suites whose
filter matched, and core runs only for frontend changes.

The connector paths in the filters file were checked with an import graph
(grimp over backend/python/app, ignoring the connector registry, which imports
every connector): s3 is imported by minio, web by rss, github_teams by github,
and google / atlassian / microsoft / local_fs / network_share / localKB by
shared code. Recheck when a connector starts importing another one.

Stdlib only: main.yml runs the scripts tests without installing anything.
"""

from __future__ import annotations

import json
import os
import re
from typing import Mapping

NOT_MARKERS = {"frontend", "no_stack", "all", "renamed"}
ALWAYS = ["core", "demo"]


def _files(outputs: Mapping[str, str], name: str) -> set[str]:
    return set(json.loads(outputs.get(f"{name}_files") or "[]"))


def connector_shards(env: Mapping[str, str]) -> dict[str, list[str]]:
    shards = {}
    for key, value in env.items():
        number = re.fullmatch(r"CONN_SHARD_(\d+)", key)
        if number:
            shards[f"connectors-{number.group(1)}"] = [m.strip() for m in value.split(" or ") if m.strip()]
    return dict(sorted(shards.items(), key=lambda item: int(item[0].rsplit("-", 1)[1])))


def select(outputs: Mapping[str, str], env: Mapping[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Return (shards, markers, unmatched files). Empty markers means a full run."""
    shards = connector_shards(env)
    # By value, not just suffix: the azure_files filter's own true/false output ends in _files too.
    names = {
        key[: -len("_files")]
        for key, value in outputs.items()
        if key.endswith("_files") and str(value).startswith("[")
    } - {"all", "renamed"}
    matched = {name: _files(outputs, name) for name in names}
    unmatched = sorted(_files(outputs, "all") - set().union(*matched.values()))

    if unmatched or outputs.get("renamed") == "true":
        return [*shards, *ALWAYS], [], unmatched

    markers = sorted(name for name, files in matched.items() if files and name not in NOT_MARKERS)
    picked = [shard for shard, names in shards.items() if set(names) & set(markers)]
    if matched.get("frontend"):
        picked.append("core")
    return picked, markers, unmatched


def main() -> None:
    shards, markers, unmatched = select(json.loads(os.environ["FILTER_OUTPUTS"]), os.environ)
    # File names come from the pull request: log them JSON-encoded on a line of our own.
    print(f"Changed files no filter names (each forces a full run): {json.dumps(unmatched)}")
    print(f"Shards: {json.dumps(shards)}  Markers: {' '.join(markers) or '(all)'}")
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
        out.write(f"shards={json.dumps(shards)}\nmarkers={' '.join(markers)}\n")


if __name__ == "__main__":
    main()
