#!/usr/bin/env bash
# Checks a built app image. PIPESHUB_IMAGE is the image under test.
# PIPESHUB_IMAGE_VARIANT is slim (default) or full.
set -euo pipefail

IMAGE="${PIPESHUB_IMAGE:?set PIPESHUB_IMAGE to the image to check}"
VARIANT="${PIPESHUB_IMAGE_VARIANT:-slim}"

echo "image structure: ${IMAGE} (${VARIANT})"

docker run --rm --entrypoint python "$IMAGE" - <<'PY'
import importlib.metadata as metadata
import os
from collections import defaultdict
from pathlib import Path

node_modules = Path("/app/backend/dist/node_modules")
for forbidden in ("bun", "@oven", "aws-sdk", "@img/sharp-wasm32"):
    path = node_modules / forbidden
    if path.exists():
        raise SystemExit(f"{path} must not be in the image")

junk = []
if node_modules.is_dir():
    for dirpath, _dirs, files in os.walk(node_modules):
        for name in files:
            if name.endswith((".d.ts", ".d.mts", ".d.cts", ".map")):
                junk.append(os.path.join(dirpath, name))
                if len(junk) > 5:
                    break
        if len(junk) > 5:
            break
if junk:
    raise SystemExit("node_modules still contains type declarations or source maps: " + ", ".join(junk[:5]))

site = Path("/usr/local/lib/python3.12/site-packages")
for child in site.glob("opencv_python-*.dist-info"):
    raise SystemExit(f"non-headless OpenCV is installed: {child.name}")

owners: dict[str, list[str]] = defaultdict(list)
for dist in metadata.distributions():
    name = dist.metadata["Name"] or dist.name
    top_level = dist.read_text("top_level.txt")
    if not top_level:
        continue
    for line in top_level.splitlines():
        module = line.strip()
        if module:
            owners[module].append(name)

allow = {"litellm"}
dupes = {module: names for module, names in owners.items() if len(set(names)) > 1 and module not in allow}
if dupes:
    shown = ", ".join(f"{module}: {sorted(set(names))}" for module, names in list(dupes.items())[:8])
    raise SystemExit(f"top-level modules owned by more than one distribution: {shown}")
print("package layout: ok")

import importlib
for module in ("docling", "sentence_transformers", "crawl4ai", "fastembed", "cv2"):
    importlib.import_module(module)

import torch
if "+cpu" not in torch.__version__:
    raise SystemExit(f"torch is not the CPU build: {torch.__version__}")
banned = [
    dist.metadata["Name"]
    for dist in metadata.distributions()
    if (dist.metadata["Name"] or "").startswith(("nvidia-", "triton"))
]
if banned:
    raise SystemExit(f"CUDA packages are installed: {banned[:8]}")

import subprocess
subprocess.run([os.environ.get("PYTHON", "python"), "-m", "pip", "check"], check=True)

nltk = list(Path("/root/nltk_data").rglob("*punkt*"))
if not nltk:
    raise SystemExit("nltk punkt data is missing")
if not Path("/root/.cache/fastembed").is_dir():
    raise SystemExit("fastembed BM25 cache is missing")
print("runtime imports: ok")
PY

docker run --rm --entrypoint bash "$IMAGE" -c '
set -euo pipefail
test -f /app/backend/dist/public/index.html
test -f /app/backend/src/modules/api-docs/pipeshub-openapi.yaml
test -x /usr/local/bin/docker-entrypoint.sh
soffice --version >/dev/null
cypher-shell --version >/dev/null
node -e "const v=process.versions.node; if(!v.startsWith(\"22.\")) { console.error(v); process.exit(1); } require(\"/app/backend/dist/node_modules/sharp\");"
for tool in gcc cargo rustc; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool must not be in the runtime image" >&2
    exit 1
  fi
done
echo "runtime tools: ok"
'

uid="$(docker run --rm "$IMAGE" id -u | tr -d "[:space:]")"
if [ "$uid" != "1000" ]; then
  echo "entrypoint ran as uid ${uid}, expected 1000" >&2
  exit 1
fi

# Paths the runtime user writes at startup: the process monitor log, the Node
# server's working directory, Python logs, the data volume and the model caches.
docker run --rm "$IMAGE" sh -c '
set -e
touch /app/process_monitor.log /app/backend/.write-check /app/python/logs/.write-check
touch /data/pipeshub/.write-check /root/.cache/.write-check
for dir in /root/.cache/fastembed /root/nltk_data; do touch "$dir/.write-check"; done
echo "runtime paths writable: ok"
'

if [ "$VARIANT" = "slim" ]; then
  docker run --rm --entrypoint sh "$IMAGE" -c \
    '! find /root/.cache/ms-playwright /data/pipeshub/ms-playwright -type f \( -name chrome -o -name headless_shell -o -name chromium \) 2>/dev/null | grep -q .'
  docker run --rm --entrypoint sh "$IMAGE" -c \
    '! test -d /root/.cache/huggingface/hub || ! find /root/.cache/huggingface -iname "*bge*" | grep -q .'
else
  docker run --rm --entrypoint sh "$IMAGE" -c \
    'find /root/.cache/huggingface -iname "*bge-large*" | grep -q . && find /root/.cache/huggingface -iname "*bge-reranker*" | grep -q .'
  docker run --rm --network none --shm-size=1g --entrypoint python "$IMAGE" - <<'PY'
import asyncio
import http.server
import socketserver
import threading

from playwright.async_api import async_playwright

from app.connectors.sources.web.crawl4ai_fetcher import Crawl4AIFetcher

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"pipeshub-offline-crawl"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, fmt, *args):
        return

server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

async def run():
    # The web connector's address guard refuses loopback, so the page load uses
    # Playwright directly. Crawl4AIFetcher.start() is still the product launch path.
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"http://127.0.0.1:{port}/")
        text = await page.inner_text("body")
        await browser.close()
    if "pipeshub-offline-crawl" not in text:
        raise SystemExit(f"full image browser did not see the local page: {text[:200]!r}")
    fetcher = Crawl4AIFetcher(concurrency=1)
    await fetcher.start(browser_like=False)
    await fetcher.close()

asyncio.run(run())
server.shutdown()
print("full offline crawl: ok")
PY
fi

if [ "$VARIANT" = "slim" ]; then
  docker run --rm --shm-size=1g --entrypoint python "$IMAGE" - <<'PY'
import asyncio
import http.server
import socketserver
import threading

from playwright.async_api import async_playwright

from app.connectors.sources.web.crawl4ai_fetcher import Crawl4AIFetcher
from app.utils.browser_install import ensure_chromium

asyncio.run(ensure_chromium())

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"pipeshub-slim-crawl"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, fmt, *args):
        return

server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

async def run():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(f"http://127.0.0.1:{port}/")
        text = await page.inner_text("body")
        await browser.close()
    if "pipeshub-slim-crawl" not in text:
        raise SystemExit(f"slim browser did not see the local page: {text[:200]!r}")
    fetcher = Crawl4AIFetcher(concurrency=1)
    await fetcher.start(browser_like=False)
    await fetcher.close()

asyncio.run(run())
server.shutdown()
print("slim on-demand crawl: ok")
PY
fi

# Docling layout models download on first use. Both variants need network for that.
docker run --rm --entrypoint python "$IMAGE" - <<'PY'
from pathlib import Path
pdf = Path("/tmp/sample.pdf")
pdf.write_bytes(
    b"%PDF-1.1\n1 0 obj<<>>endobj\n2 0 obj<< /Length 44 >>stream\nBT /F1 12 Tf 100 700 Td (Hello) Tj ET\nendstream\nendobj\n"
    b"3 0 obj<< /Type /Page /Parent 4 0 R /Contents 2 0 R >>endobj\n"
    b"4 0 obj<< /Type /Pages /Kids [3 0 R] /Count 1 >>endobj\n"
    b"5 0 obj<< /Type /Catalog /Pages 4 0 R >>endobj\n"
    b"trailer<< /Root 5 0 R >>\n%%EOF\n"
)
from docling.document_converter import DocumentConverter
result = DocumentConverter().convert(str(pdf))
text = result.document.export_to_markdown()
if not text or "Hello" not in text:
    raise SystemExit(f"docling did not read the sample PDF: {text!r}")
print("docling pdf: ok")
PY

# Uncompressed size budgets. Estimates plus margin.
python3 - "$IMAGE" "$VARIANT" <<'PY'
import json, subprocess, sys
image, variant = sys.argv[1], sys.argv[2]
info = json.loads(subprocess.check_output(["docker", "image", "inspect", image]))[0]
size = int(info["Size"])
limit = 7 * 1024**3 if variant == "slim" else 10 * 1024**3
print(f"uncompressed size: {size / 1024**3:.2f} GiB (limit {limit / 1024**3:.0f} GiB)")
if size > limit:
    raise SystemExit(f"{variant} image is {size} bytes, over the {limit} byte budget")
PY

echo "image_structure_test: ok"
