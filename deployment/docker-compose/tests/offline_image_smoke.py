"""Runs inside an app image started with --network none and PIPESHUB_OFFLINE=1.

Exits non-zero at the first thing that does not behave offline, and at the end if anything
*attempted* an outbound connection: with the network removed every connect fails fast, and
libraries that tolerate that (a price-list fetch, a telemetry client) would otherwise pass in
silence. An audit hook records every non-loopback connect or name lookup.
Needs no store: it checks the switches, the baked tokenizer, LiteLLM's import, the telemetry gate,
the embedding service's start, and that a PDF parses with the baked Docling models. On the full
image it also loads the baked embedding model and embeds a string; on the slim image it checks the
offline refusal.

    python offline_image_smoke.py <slim|full>

Run by offline_image_smoke.sh (CI) with the image's own Python.
"""

# ruff: noqa: T201, FBT001
import asyncio
import ipaddress
import logging
import os
import socket
import sys
from io import BytesIO

ATTEMPTS: list[str] = []


def is_local(event: str, target: object) -> bool:
    """True for a Unix socket, an empty host, ``localhost`` and a loopback or unspecified address.

    Exact matches only: ``localhost.example.com`` and ``127.evil.example`` are external names, so a
    prefix test would let a lookup for them pass unrecorded."""
    if event == "socket.connect" and isinstance(target, (str, bytes)):
        return True  # AF_UNIX path (or abstract name)
    host = target[0] if isinstance(target, (tuple, list)) and target else target
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host is None or host == "":
        return True
    name = str(host).strip().lower().rstrip(".")
    if name == "localhost":
        return True
    try:
        address = ipaddress.ip_address(name.split("%", 1)[0].strip("[]"))
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return (mapped or address).is_loopback or address.is_unspecified


def _audit(event: str, args: tuple) -> None:
    if event not in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):
        return
    target = args[1] if event == "socket.connect" and len(args) > 1 else args[0]
    if is_local(event, target):
        return
    ATTEMPTS.append(f"{event}:{target[0] if isinstance(target, (tuple, list)) else target}")


# Before anything imports: a library that connects at import time is caught too.
if __name__ == "__main__":
    sys.addaudithook(_audit)

EXPECTED = {
    "PIPESHUB_OFFLINE": "1",
    "TELEMETRY_COLLECTION_FLAG": "false",
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "LITELLM_LOCAL_MODEL_COST_MAP": "True",
    "DO_NOT_TRACK": "1",
}


def step(name: str) -> None:
    print(f"== {name}", flush=True)


def check(condition: bool, message: str) -> None:
    if not condition:
        print(f"FAIL: {message}", file=sys.stderr, flush=True)
        sys.exit(1)
    print(f"ok: {message}", flush=True)


def no_network() -> None:
    step("the container really has no route out")
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3).close()
    except OSError:
        ATTEMPTS.clear()  # the probe above is this script's own attempt
        check(True, "outbound connections fail")
        return
    check(False, "an outbound connection succeeded; --network none was not applied")


def switches() -> None:
    step("the entrypoint set the offline switches")
    for name, value in EXPECTED.items():
        check(os.environ.get(name) == value, f"{name}={value}")


def tokenizer() -> None:
    step("cl100k_base loads from the image")
    import tiktoken

    check(os.environ.get("TIKTOKEN_CACHE_DIR") == "/opt/tiktoken", "TIKTOKEN_CACHE_DIR=/opt/tiktoken")
    check(len(tiktoken.get_encoding("cl100k_base").encode("hello world")) == 2, "encodes without a download")


def litellm_import() -> None:
    step("LiteLLM imports with its bundled price list")
    import litellm  # noqa: F401

    check(True, "import litellm")


def telemetry() -> None:
    step("telemetry is off")
    from app.telemetry.flag import telemetry_enabled

    check(telemetry_enabled() is False, "telemetry_enabled() is False")


def embedding_service(variant: str) -> None:
    step("the embedding service starts and behaves offline")
    from fastapi.testclient import TestClient

    from app import embedding_main

    with TestClient(embedding_main.app) as client:
        check(client.get("/health").status_code == 200, "embedding /health answers")
    manager = embedding_main.ModelManager(device="cpu")
    if variant == "full":
        vectors = asyncio.run(_embed(manager))
        check(len(vectors) == 1 and len(vectors[0]) > 0, "the baked embedding model loads and embeds one string")
        return
    try:
        asyncio.run(manager.get_model(embedding_main.DEFAULT_EMBEDDING_MODEL))
    except RuntimeError as exc:
        check("is not in this image and cannot be downloaded" in str(exc), "the refusal names the missing model")
    else:
        check(False, "loading the default embedding model succeeded on a slim image")


async def _embed(manager: object) -> list:
    from app import embedding_main

    async with manager._lease(embedding_main.DEFAULT_EMBEDDING_MODEL) as model:
        return model.encode(["offline smoke"]).tolist()


def sample_pdf() -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont("Helvetica", 14)
    pdf.drawString(72, 760, "Offline smoke document")
    pdf.drawString(72, 730, "The quick brown fox jumps over the lazy dog.")
    pdf.save()
    return buffer.getvalue()


def parse_pdf() -> None:
    step("a PDF parses with the baked Docling models")
    from app.modules.parsers.pdf.docling_processor import DoclingProcessor

    processor = DoclingProcessor(logging.getLogger("offline-smoke"), config=None)
    document = asyncio.run(processor.parse_document("smoke.pdf", sample_pdf()))
    text = document.export_to_markdown()
    check("Offline smoke document" in text and "quick brown fox" in text, "the parsed text contains the page text")


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "slim"
    if variant not in ("slim", "full"):
        sys.exit("usage: offline_image_smoke.py <slim|full>")
    for run in (no_network, switches, tokenizer, litellm_import, telemetry):
        run()
    embedding_service(variant)
    parse_pdf()
    check(not ATTEMPTS, f"no outbound connection was attempted (saw: {sorted(set(ATTEMPTS))})")
    print(f"offline smoke passed ({variant})")
