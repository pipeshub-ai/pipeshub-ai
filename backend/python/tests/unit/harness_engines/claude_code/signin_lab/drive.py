"""Runs inside the Claude Code container (SIGNIN-1): one step of the login flow, over plain pipes as the sandbox
broker's ``exec`` gives them (no terminal). Prints one JSON record. Standard library only.

    drive.py status | login CODE [raw] | logout | prompt
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse

CONFIG = os.environ["CLAUDE_CONFIG_DIR"]
PROMPT = "Paste code here if prompted"


def tree() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for root, dirs, files in os.walk(CONFIG):
        for name in dirs + files:
            path = os.path.join(root, name)
            rows.append(
                {
                    "path": os.path.relpath(path, CONFIG),
                    "mode": oct(os.lstat(path).st_mode & 0o7777),
                    "dir": os.path.isdir(path),
                }
            )
    return sorted(rows, key=lambda row: row["path"])


def shape(value: object) -> object:
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(v) for v in value]
    return type(value).__name__


def run(argv: list[str], timeout: float = 90) -> dict[str, object]:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return {
        "argv": argv,
        "rc": done.returncode,
        "stdout": done.stdout,
        "stderr": done.stderr,
    }


def login(code: str, raw: bool) -> dict[str, object]:
    process = subprocess.Popen(
        ["claude", "auth", "login"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out, err = bytearray(), bytearray()
    for stream, sink in ((process.stdout, out), (process.stderr, err)):
        threading.Thread(
            target=lambda s=stream, b=sink: [
                b.extend(c) for c in iter(lambda: s.read1(4096), b"")
            ],
            daemon=True,
        ).start()
    started, url, answered = time.time(), None, None
    while time.time() - started < 40 and process.poll() is None:
        text = out.decode(errors="replace")
        if answered is None and PROMPT in text:
            url = re.search(r"https://\S+", text).group(0)
            state = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["state"][0]
            process.stdin.write((code if raw else f"{code}#{state}").encode() + b"\n")
            process.stdin.flush()
            answered = round(time.time() - started, 1)
        time.sleep(0.1)
    rc = process.poll()
    if rc is None:
        process.kill()
        process.wait()
        rc = "still_running_after_40s"
    time.sleep(0.2)
    record: dict[str, object] = {
        "rc": rc,
        "stdout": out.decode(errors="replace"),
        "stderr": err.decode(errors="replace"),
        "url": url,
        "tree": tree(),
    }
    credentials = os.path.join(CONFIG, ".credentials.json")
    if os.path.exists(credentials):
        with open(credentials) as f:
            record["credentials_shape"] = shape(json.load(f))
    config = os.path.join(CONFIG, ".claude.json")
    if os.path.exists(config):
        with open(config) as f:
            account = json.load(f).get("oauthAccount")
        record["oauth_account"] = account
    return record


step = sys.argv[1]
if step == "status":
    record = {
        "json": run(["claude", "auth", "status"]),
        "text": run(["claude", "auth", "status", "--text"]),
    }
elif step == "login":
    record = login(sys.argv[2], len(sys.argv) > 3 and sys.argv[3] == "raw")
elif step == "logout":
    record = {**run(["claude", "auth", "logout"]), "tree": tree()}
elif step == "prompt":
    record = run(
        [
            "claude",
            "-p",
            "say hi",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            "1",
        ]
    )
else:
    raise SystemExit(f"unknown step {step}")
print(json.dumps(record))
