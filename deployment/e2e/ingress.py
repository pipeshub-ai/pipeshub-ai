"""DEV-ONLY: the air-gapped lane's ingress (docker-compose.airgap.yml, E2E_AIRGAP=1, C15.04).

    python ingress.py 3000=pipeshub-ai:3000 8080=pxh-fake-llm:8080 ...

Every PipesHub container of an air-gapped stack is on internal networks only, where Docker neither routes out nor
publishes a port. This container is the one on both an internal network and a published one: it accepts what the host
sends to a published port and forwards it to the service inside, as an install's reverse proxy at the edge does. It
forwards inbound connections only; nothing inside can reach out through it.
"""

import asyncio
import re
import sys

PAIR = re.compile(r"^(\d+)=([^:]+):(\d+)$")


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


def handler(host: str, port: int):
    async def handle(client_r: asyncio.StreamReader, client_w: asyncio.StreamWriter) -> None:
        try:
            up_r, up_w = await asyncio.open_connection(host, port)
        except OSError:
            client_w.close()
            return
        await asyncio.gather(pipe(client_r, up_w), pipe(up_r, client_w))

    return handle


async def main(args: list[str]) -> None:
    servers = []
    for arg in args:
        m = PAIR.match(arg)
        if not m:
            sys.exit(f"ingress: expected <port>=<host>:<port>, got {arg}")
        servers.append(await asyncio.start_server(handler(m[2], int(m[3])), "0.0.0.0", int(m[1])))
    print(f"ingress: ready ({len(servers)} ports)", flush=True)
    await asyncio.gather(*(s.serve_forever() for s in servers))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
