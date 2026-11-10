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


def narrative(log, version):
    """The narrative paragraph of the version's CHANGELOG.md entry, or None.

    The entry is its "## <version> — <date>" heading, the "**<headline>** · ..." metadata
    paragraph, then the narrative, then "### " sections. A wrapped narrative is joined.
    """
    entry, inside = [], False
    for line in log.split("\n"):
        heading = re.match(r"(#{2,3}) (\S+)", line)
        if inside and heading:
            break  # the entry's first section, or the next entry
        if heading and heading.groups() == ("##", version):
            inside = True
        elif inside:
            entry.append(line.strip())
    paragraphs = re.split(r"\n\n+", "\n".join(entry).strip())
    if len(paragraphs) < 2 or not paragraphs[0].startswith("**"):
        return None
    text = paragraphs[1]
    if re.match(r"[#>|*+-]|\d+\.\s", text):  # a heading, quote, table or list, not prose
        return None
    return " ".join(text.split("\n"))


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

    # The docs summary page shows the release's narrative from the CHANGELOG.md entry.
    log = (src.parent.parent / "CHANGELOG.md").read_text(encoding="utf-8")
    text = narrative(log, version)
    if not text:
        sys.exit(f"CHANGELOG.md: the {version} entry has no narrative paragraph after its metadata line")
    summary = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # its links are relative to CHANGELOG.md

    # Mintlify builds heading ids differently, so linked headings keep GitHub's id explicitly.
    linked = set(re.findall(r"\]\(#([^)\s]+)\)", "\n".join(lines)))

    body, in_fence = [f"Released {month} {int(day)}, {year} · {channel} · [View on GitHub]({url})"], False
    for line in lines[3:]:
        if line.lstrip().startswith("```"):  # fences may be indented inside list items
            in_fence = not in_fence
            body.append(line)
        elif in_fence:
            body.append(line)
        else:
            # Each version has its own page: 0.9.0.md -> /changelog/platform/0.9.0.
            line = re.sub(r"\]\((\d[\w.\-]*)\.md\)", r"](/changelog/platform/\1)", line)
            heading = re.match(r"#{2,6} (.+)", line)
            anchor = heading and github_slug(heading.group(1))
            body.append(escape(line) + (f" {{#{anchor}}}" if anchor in linked else ""))

    front = ["---", f"title: {json.dumps(version)}", f"version: {json.dumps(version)}",
             f"date: {json.dumps(date)}", f"channel: {json.dumps(channel)}",
             f"release_url: {json.dumps(url)}", f"description: {json.dumps(head.group(2))}",
             f"summary: {json.dumps(summary, ensure_ascii=False)}", "---", ""]
    out = out_dir / f"{version}.mdx"
    out.write_text("\n".join(front + body).rstrip("\n") + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    out_dir = pathlib.Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    convert(pathlib.Path(sys.argv[1]), out_dir)
