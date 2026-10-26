"""A consumer process for test_consumer_kill.py: claims one message of a topic, says so, then hangs mid-message until it is killed."""

import asyncio
import json
import sys

from app.services.messaging.postgres.queue import PgQueue
from app.services.pg_runtime import RuntimeDb


async def main(topic: str, group: str, name: str, claim_s: float) -> None:
    queue = PgQueue(RuntimeDb())
    got = await queue.claim(group, [topic], name, 10, claim_s)
    print(json.dumps({"claimed": [d.attempts for d in got]}), flush=True)
    await asyncio.sleep(3600)


asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], float(sys.argv[4])))
