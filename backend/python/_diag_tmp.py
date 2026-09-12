"""Diagnose what actually landed in the graph for one repo."""
import asyncio
import collections
import sys
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from app.config.configuration_service import ConfigurationService  # noqa: E402
from app.config.constants.arangodb import CollectionNames  # noqa: E402
from app.config.providers.in_memory_store import InMemoryKeyValueStore  # noqa: E402
from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory  # noqa: E402
from app.utils.logger import create_logger  # noqa: E402

logger = create_logger("diag")
ORG = sys.argv[1]
GROUP = sys.argv[2]


class KV(InMemoryKeyValueStore):
    @property
    def client(self) -> Any:
        return None


async def main() -> None:
    cs = ConfigurationService(logger, KV(logger, "app/config/default_config.json"))
    p = await GraphDBProviderFactory.create_provider(logger, cs)

    print("\n=== RECORDS in group ===")
    recs = await p.execute_query(
        """
        MATCH (r:Record) WHERE r.orgId=$o AND r.recordGroupId=$g
        RETURN r.id AS id, r.recordName AS name, r.recordType AS type,
               r.extension AS ext, r.indexingStatus AS istat,
               r.parsingStatus AS pstat, r.mimeType AS mime
        """,
        {"o": ORG, "g": GROUP},
    ) or []
    print(f"total records: {len(recs)}")
    by_ext = collections.Counter((r.get("ext") or "?") for r in recs)
    by_stat = collections.Counter((r.get("istat") or "?") for r in recs)
    print("by extension:", dict(by_ext))
    print("by indexingStatus:", dict(by_stat))

    print("\n=== per-record detail ===")
    for r in sorted(recs, key=lambda x: str(x.get("name"))):
        print(f"  {str(r.get('name'))[:34]:36} ext={str(r.get('ext')):6} "
              f"idx={str(r.get('istat')):12} parse={str(r.get('pstat')):12} "
              f"mime={str(r.get('mime'))[:24]}")

    print("\n=== BLOCKS ===")
    blocks = await p.execute_query(
        """
        MATCH (b:Block) WHERE b.orgId=$o AND b.recordGroupId=$g
        RETURN b.filePath AS fp, b.kind AS kind, b.name AS name,
               b.language AS lang, b.pendingEdges AS pe
        """,
        {"o": ORG, "g": GROUP},
    ) or []
    print(f"total blocks: {len(blocks)}")
    print("blocks per file:", dict(collections.Counter(b.get("fp") for b in blocks)))
    print("blocks per kind:", dict(collections.Counter(b.get("kind") for b in blocks)))
    print("blocks per lang:", dict(collections.Counter(b.get("lang") for b in blocks)))

    import json
    print("\n=== PENDING EDGE breakdown ===")
    rel = collections.Counter()
    unresolved_names = collections.Counter()
    known = {b.get("name") for b in blocks if b.get("name")}
    for b in blocks:
        pe = b.get("pe")
        if isinstance(pe, str):
            try:
                pe = json.loads(pe)
            except Exception:
                pe = []
        for e in pe or []:
            rel[e.get("relation")] += 1
            if e.get("toName") not in known:
                unresolved_names[e.get("toName")] += 1
    print("pending by relation:", dict(rel))
    print(f"\ntop 30 target names NOT defined anywhere in the indexed corpus "
          f"({sum(unresolved_names.values())} of {sum(rel.values())} total):")
    for name, n in unresolved_names.most_common(30):
        print(f"   {n:4}  {name}")

    dis = getattr(p, "disconnect", None)
    if dis:
        await dis()


asyncio.run(main())
