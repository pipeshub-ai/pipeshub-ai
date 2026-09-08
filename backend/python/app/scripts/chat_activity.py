#!/usr/bin/env python3
"""Extract searches, file reads, and terminal commands from Cursor agent transcripts.

Usage:
    # By session UUID
    python chat_activity.py 91151886-c646-4770-a0e3-4bc9f6d86675

    # List recent sessions (default 10)
    python chat_activity.py --list
    python chat_activity.py --list 30

    # Search sessions by keyword
    python chat_activity.py --search "slack connector"

    # Filter by tool type
    python chat_activity.py <uuid> --only shell,grep

    # JSON output
    python chat_activity.py <uuid> --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

TRANSCRIPTS_ROOT = Path.home() / ".cursor" / "projects"

TOOL_CATEGORIES = {
    "search": ["Grep", "Glob"],
    "read": ["Read"],
    "shell": ["Shell"],
    "edit": ["StrReplace", "Write", "Delete", "EditNotebook"],
    "meta": ["TodoWrite", "ReadLints", "AwaitShell", "WebSearch", "WebFetch"],
}


def find_project_dirs() -> list[Path]:
    if not TRANSCRIPTS_ROOT.exists():
        return []
    return sorted(
        (d / "agent-transcripts" for d in TRANSCRIPTS_ROOT.iterdir()
         if (d / "agent-transcripts").is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def iter_sessions(project_dir: Path):
    for d in project_dir.iterdir():
        jsonl = d / f"{d.name}.jsonl"
        if jsonl.is_file():
            yield d.name, jsonl


def get_preview(jsonl_path: Path, max_len: int = 100) -> str:
    with open(jsonl_path) as f:
        for line in f:
            o = json.loads(line)
            if o.get("role") != "user":
                continue
            msg = o.get("message", {})
            parts = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(parts, list):
                continue
            text = " ".join(
                p.get("text", "") for p in parts if isinstance(p, dict) and p.get("type") == "text"
            )
            if "<user_query>" in text:
                text = text.split("<user_query>", 1)[1].split("</user_query>", 1)[0]
            text = re.sub(r"<[^>]+>", "", text)
            return " ".join(text.split())[:max_len]
    return "(empty)"


def parse_jsonl(jsonl_path: Path) -> list[dict]:
    actions: list[dict] = []
    with open(jsonl_path) as f:
        for line in f:
            o = json.loads(line)
            msg = o.get("message", {})
            content = msg.get("content") if isinstance(msg, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "tool_use":
                    continue
                name = part.get("name", "")
                inp = part.get("input", {})
                action = extract_action(name, inp)
                if action:
                    actions.append(action)
    return actions


def parse_session(session_dir: Path) -> tuple[list[dict], list[dict]]:
    """Returns (main_actions, subagent_groups).

    Each subagent group is a dict with keys: id, description, subagent_type, actions.
    """
    main_jsonl = session_dir / f"{session_dir.name}.jsonl"
    main_actions = parse_jsonl(main_jsonl) if main_jsonl.is_file() else []

    task_meta: dict[str, dict] = {}
    if main_jsonl.is_file():
        with open(main_jsonl) as f:
            for line in f:
                o = json.loads(line)
                msg = o.get("message", {})
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                for part in content:
                    if (isinstance(part, dict)
                            and part.get("type") == "tool_use"
                            and part.get("name") == "Task"):
                        inp = part.get("input", {})
                        tid = part.get("id", "")
                        task_meta[tid] = {
                            "description": inp.get("description", ""),
                            "subagent_type": inp.get("subagent_type", ""),
                        }

    subagent_groups: list[dict] = []
    subagents_dir = session_dir / "subagents"
    if subagents_dir.is_dir():
        for sa_jsonl in sorted(subagents_dir.glob("*.jsonl"),
                               key=lambda p: p.stat().st_mtime):
            sa_id = sa_jsonl.stem
            actions = parse_jsonl(sa_jsonl)
            meta = task_meta.get(sa_id, {})
            label = get_preview(sa_jsonl, 80) if not meta.get("description") else ""
            subagent_groups.append({
                "id": sa_id,
                "description": meta.get("description", label),
                "subagent_type": meta.get("subagent_type", ""),
                "actions": actions,
            })

    return main_actions, subagent_groups


def extract_action(name: str, inp: dict) -> dict | None:
    if name == "Grep":
        return {
            "tool": "Grep",
            "category": "search",
            "pattern": inp.get("pattern", ""),
            "path": inp.get("path", ""),
            "glob": inp.get("glob", ""),
            "flags": {k: v for k, v in inp.items() if k.startswith("-")},
        }
    if name == "Glob":
        return {
            "tool": "Glob",
            "category": "search",
            "glob_pattern": inp.get("glob_pattern", ""),
            "target_directory": inp.get("target_directory", ""),
        }
    if name == "Read":
        return {
            "tool": "Read",
            "category": "read",
            "path": inp.get("path", ""),
            "offset": inp.get("offset"),
            "limit": inp.get("limit"),
        }
    if name == "Shell":
        return {
            "tool": "Shell",
            "category": "shell",
            "command": inp.get("command", ""),
            "description": inp.get("description", ""),
            "working_directory": inp.get("working_directory", ""),
        }
    if name == "StrReplace":
        return {
            "tool": "StrReplace",
            "category": "edit",
            "path": inp.get("path", ""),
            "old_string_preview": (inp.get("old_string", ""))[:80],
            "new_string_preview": (inp.get("new_string", ""))[:80],
        }
    if name == "Write":
        return {
            "tool": "Write",
            "category": "edit",
            "path": inp.get("path", ""),
            "size": len(inp.get("contents", "")),
        }
    if name == "Delete":
        return {
            "tool": "Delete",
            "category": "edit",
            "path": inp.get("path", ""),
        }
    if name == "WebSearch":
        return {
            "tool": "WebSearch",
            "category": "meta",
            "search_term": inp.get("search_term", ""),
        }
    if name == "WebFetch":
        return {
            "tool": "WebFetch",
            "category": "meta",
            "url": inp.get("url", ""),
        }
    if name == "Task":
        return {
            "tool": "Task",
            "category": "meta",
            "description": inp.get("description", ""),
            "subagent_type": inp.get("subagent_type", ""),
            "prompt_len": len(inp.get("prompt", "")),
        }
    return None


def shorten_path(p: str, max_parts: int = 4) -> str:
    parts = Path(p).parts
    if len(parts) <= max_parts:
        return p
    return str(Path("...") / Path(*parts[-max_parts:]))


def format_action(a: dict) -> str:
    tool = a["tool"]
    if tool == "Grep":
        loc = a["path"] or a.get("glob") or "(workspace)"
        flags = " ".join(f"{k}={v}" for k, v in a.get("flags", {}).items())
        extra = f" {flags}" if flags else ""
        return f"  grep /{a['pattern']}/{extra}  in {shorten_path(loc)}"
    if tool == "Glob":
        target = a.get("target_directory") or "(workspace)"
        return f"  glob {a['glob_pattern']}  in {shorten_path(target)}"
    if tool == "Read":
        parts = [shorten_path(a["path"])]
        if a.get("offset"):
            parts.append(f"offset={a['offset']}")
        if a.get("limit"):
            parts.append(f"limit={a['limit']}")
        return f"  read {' '.join(parts)}"
    if tool == "Shell":
        cmd = a["command"].replace("\n", " \\ ")
        if len(cmd) > 120:
            cmd = cmd[:117] + "..."
        desc = f"  # {a['description']}" if a.get("description") else ""
        wd = f"  (in {shorten_path(a['working_directory'])})" if a.get("working_directory") else ""
        return f"  $ {cmd}{desc}{wd}"
    if tool == "StrReplace":
        return f"  edit {shorten_path(a['path'])}"
    if tool == "Write":
        return f"  write {shorten_path(a['path'])} ({a['size']} bytes)"
    if tool == "Delete":
        return f"  delete {shorten_path(a['path'])}"
    if tool == "WebSearch":
        return f"  web-search \"{a['search_term']}\""
    if tool == "WebFetch":
        return f"  web-fetch {a['url']}"
    if tool == "Task":
        return f"  task [{a.get('subagent_type','')}] \"{a['description']}\""
    return f"  {tool} {json.dumps(a)[:100]}"


def cmd_list(args):
    limit = int(args.list_count) if args.list_count else 10
    project_dirs = find_project_dirs()
    if not project_dirs:
        print("No agent-transcripts found under", TRANSCRIPTS_ROOT)
        return

    for pdir in project_dirs:
        project_name = pdir.parent.name
        sessions = sorted(
            iter_sessions(pdir),
            key=lambda s: s[1].stat().st_mtime,
            reverse=True,
        )[:limit]
        if not sessions:
            continue
        print(f"\n{project_name}  ({len(list(iter_sessions(pdir)))} total)")
        print("-" * 90)
        for sid, jsonl in sessions:
            mtime = datetime.fromtimestamp(jsonl.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            preview = get_preview(jsonl, max_len=70)
            print(f"  {sid}  {mtime}  {preview}")


def cmd_search(args):
    query = args.search_query.lower()
    project_dirs = find_project_dirs()
    hits = []
    for pdir in project_dirs:
        for sid, jsonl in iter_sessions(pdir):
            try:
                text = jsonl.read_text(errors="replace").lower()
            except OSError:
                continue
            if query in text:
                preview = get_preview(jsonl, 80)
                hits.append((pdir.parent.name, sid, preview))
    if not hits:
        print(f"No sessions matching \"{args.search_query}\"")
        return
    print(f"Found {len(hits)} session(s) matching \"{args.search_query}\":\n")
    for proj, sid, preview in hits[:30]:
        print(f"  {sid}  [{proj}]  {preview}")


def apply_filter(actions: list[dict], only_str: str | None) -> list[dict]:
    if not only_str:
        return actions
    only: set[str] = set()
    for token in only_str.split(","):
        token = token.strip().lower()
        if token in TOOL_CATEGORIES:
            only.update(t.lower() for t in TOOL_CATEGORIES[token])
        else:
            only.add(token)
    return [a for a in actions
            if a["tool"].lower() in only or a["category"] in only]


SECTION_TITLES = {
    "search": "Searches (Grep / Glob)",
    "read": "File Reads",
    "shell": "Terminal Commands",
    "edit": "Edits (StrReplace / Write / Delete)",
    "meta": "Other (Web, Tasks, Lints, Todos)",
}


def print_actions(actions: list[dict], indent: str = ""):
    by_cat: dict[str, list[dict]] = {}
    for a in actions:
        by_cat.setdefault(a["category"], []).append(a)
    for cat in ["search", "read", "shell", "edit", "meta"]:
        items = by_cat.get(cat, [])
        if not items:
            continue
        print(f"{indent}{'=' * 60}")
        print(f"{indent} {SECTION_TITLES.get(cat, cat)}  ({len(items)})")
        print(f"{indent}{'=' * 60}")
        for a in items:
            print(f"{indent}{format_action(a)}")
        print()


def cmd_show(args):
    session_id = args.session_id
    session_dir = None
    for pdir in find_project_dirs():
        candidate = pdir / session_id
        if (candidate / f"{session_id}.jsonl").is_file():
            session_dir = candidate
            break

    if not session_dir:
        print(f"Session not found: {session_id}")
        print("Run with --list to see available sessions.")
        sys.exit(1)

    main_actions, subagent_groups = parse_session(session_dir)
    main_actions = apply_filter(main_actions, args.only)
    for sg in subagent_groups:
        sg["actions"] = apply_filter(sg["actions"], args.only)

    all_count = len(main_actions) + sum(len(sg["actions"]) for sg in subagent_groups)

    if args.json:
        payload = {
            "session_id": session_id,
            "main": main_actions,
            "subagents": [
                {"id": sg["id"], "description": sg["description"],
                 "subagent_type": sg["subagent_type"], "actions": sg["actions"]}
                for sg in subagent_groups
            ],
        }
        json.dump(payload, sys.stdout, indent=2)
        print()
        return

    jsonl_path = session_dir / f"{session_id}.jsonl"
    preview = get_preview(jsonl_path, 100)
    print(f"Session: {session_id}")
    print(f"Preview: {preview}")
    print(f"Actions: {all_count} total ({len(main_actions)} main"
          + (f", {all_count - len(main_actions)} across {len(subagent_groups)} subagent(s))"
             if subagent_groups else ")"))
    print()

    if main_actions:
        print_actions(main_actions)

    for sg in subagent_groups:
        if not sg["actions"]:
            continue
        desc = sg["description"] or sg["id"]
        sa_type = f" [{sg['subagent_type']}]" if sg.get("subagent_type") else ""
        print(f"{'#' * 60}")
        print(f" Subagent{sa_type}: {desc}")
        print(f" {len(sg['actions'])} action(s)")
        print(f"{'#' * 60}")
        print()
        print_actions(sg["actions"], indent="  ")


def main():
    parser = argparse.ArgumentParser(
        description="Extract tool activity from Cursor agent transcripts",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("session_id", nargs="?", help="Session UUID to inspect")
    parser.add_argument("--list", dest="list_count", nargs="?", const="10",
                        help="List recent sessions (default 10)")
    parser.add_argument("--search", dest="search_query", help="Search sessions by keyword")
    parser.add_argument("--only", help="Comma-separated tool/category filter: search,read,shell,edit,meta,Grep,Shell,...")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.list_count is not None:
        cmd_list(args)
    elif args.search_query:
        cmd_search(args)
    elif args.session_id:
        cmd_show(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
