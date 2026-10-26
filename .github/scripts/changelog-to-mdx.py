#!/usr/bin/env python3
"""Convert changelog/<version>.md (release-changelog skill output) into the docs-repo
format: changelog/platform/<version>.mdx with frontmatter, MDX-safe text.

Usage: changelog-to-mdx.py <changelog/VERSION.md> <output-dir>
Format contract: scripts/changelog/SPEC.md in pipeshub-ai/documentation.
"""
import datetime
import json
import pathlib
import re
import sys

MONTHS = {m: i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], 1)}
CHANNELS = {"Stable", "Beta", "Alpha", "Pre-release"}


def escape(line):
    """Escape MDX-special characters outside inline code spans."""
    out = []
    for part in re.split(r"(`[^`]*`)", line):
        if len(part) > 1 and part.startswith("`") and part.endswith("`"):
            out.append(part)
            continue
        part = re.sub(r"<(https?://[^>\s]+)>", r"\1", part)
        out.append(part.replace("<", "&lt;").replace("{", "\\{").replace("}", "\\}"))
    return "".join(out)


def github_slug(heading):
    """Anchor GitHub gives a Markdown heading, which the source notes link to."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def convert(src, out_dir):
    version = src.stem
    lines = src.read_text(encoding="utf-8").split("\n")
    head = re.match(r"# (\S+) — (.+)", lines[0])
    if not head or head.group(1) != version:
        sys.exit(f"{src}: first line must be '# {version} — <headline>', got: {lines[0]!r}")
    meta = re.match(
        r"\*\*Released:\*\* (\w+) (\d+), (\d+) · \*\*Channel:\*\* ([^·]+?) · \*\*Tag:\*\* \[`([^`]+)`\]\(([^)]+)\)",
        lines[2])
    if not meta:
        sys.exit(f"{src}: cannot read the metadata line: {lines[2]!r}")
    month, day, year, channel, tag, url = meta.groups()
    if tag.removeprefix("v") != version:
        sys.exit(f"{src}: tag {tag!r} does not match version {version!r}")
    if url != f"https://github.com/pipeshub-ai/pipeshub-ai/releases/tag/{tag}":
        sys.exit(f"{src}: release URL {url!r} is not the release page of tag {tag!r}")
    channel = channel.strip()
    if channel not in CHANNELS or month not in MONTHS:
        sys.exit(f"{src}: unknown channel {channel!r} or month {month!r}")
    date = datetime.date(int(year), MONTHS[month], int(day)).isoformat()

    # Mintlify builds heading ids differently and numbers repeats across all versions
    # on the page, so linked headings get an explicit id that is unique per version.
    linked = set(re.findall(r"\]\(#([^)\s]+)\)", "\n".join(lines)))
    suffix = re.sub(r"[^0-9a-z]+", "-", version.lower())
    ids = {a: f"{a}-{suffix}" for a in linked}  # kept as is so distinct anchors stay distinct

    body, in_fence = [], False
    for line in lines[3:]:
        if line.lstrip().startswith("```"):  # fences may be indented inside list items
            in_fence = not in_fence
            body.append(line)
        elif in_fence:
            body.append(line)
        else:
            line = re.sub(r"\]\(#([^)\s]+)\)", lambda m: f"](#{ids[m.group(1)]})", line)
            # Every version is on one page, so a version link is an in-page anchor: 0.6.0-beta -> #v0-6-0-beta.
            line = re.sub(r"\]\((\d[\w.\-]*)\.md\)",
                          lambda m: "](#v" + re.sub(r"[^0-9a-z]+", "-", m.group(1).lower()) + ")", line)
            heading = re.match(r"#{2,6} (.+)", line)
            anchor = heading and github_slug(heading.group(1))
            line = escape(line) + (f" {{#{ids[anchor]}}}" if anchor in ids else "")
            # The page uses ## for the version, so sections move down one level.
            body.append("#" + line if heading else line)

    front = ["---", f"title: {json.dumps(version)}", f"version: {json.dumps(version)}",
             f"date: {json.dumps(date)}", f"channel: {json.dumps(channel)}",
             f"release_url: {json.dumps(url)}", f"summary: {json.dumps(head.group(2))}", "---", ""]
    out = out_dir / f"{version}.mdx"
    out.write_text("\n".join(front + body).rstrip("\n") + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    out_dir = pathlib.Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    convert(pathlib.Path(sys.argv[1]), out_dir)
