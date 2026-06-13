from __future__ import annotations

import re
from typing import Any

from app.connectors.sources.atlassian.jira.enrichment.field_registry import (
    CUSTOM_FIELD_LABELS,
    CUSTOM_FIELD_SCHEMAS,
    LIVE_SYSTEM_FIELDS,
    system_field_label,
)
_SPRINT_NAME_RE = re.compile(r"name=([^,\]]+)")


def _format_person(user_ref: dict[str, Any] | None) -> str | None:
    if not user_ref:
        return None
    name = user_ref.get("displayName") or user_ref.get("name") or user_ref.get("key")
    email = user_ref.get("emailAddress")
    if name and email:
        return f"{name} ({email})"
    return name or email


def _format_status(status_obj: Any) -> str | None:
    if isinstance(status_obj, dict):
        return status_obj.get("name")
    return str(status_obj) if status_obj else None


def _format_priority(priority_obj: Any) -> str | None:
    if isinstance(priority_obj, dict):
        return priority_obj.get("name")
    return str(priority_obj) if priority_obj else None


def _format_issuetype(type_obj: Any) -> str | None:
    if isinstance(type_obj, dict):
        return type_obj.get("name")
    return str(type_obj) if type_obj else None


def _format_resolution(res_obj: Any) -> str | None:
    if isinstance(res_obj, dict):
        return res_obj.get("name")
    return str(res_obj) if res_obj else None


def _format_project(project_obj: Any) -> str | None:
    if isinstance(project_obj, dict):
        key = project_obj.get("key")
        name = project_obj.get("name")
        if key and name:
            return f"{key} — {name}"
        return key or name
    return str(project_obj) if project_obj else None


def _format_parent(parent_obj: Any) -> str | None:
    if isinstance(parent_obj, dict):
        key = parent_obj.get("key")
        fields = parent_obj.get("fields") or {}
        summary = fields.get("summary") or parent_obj.get("summary")
        if key and summary:
            return f"{key} — {summary}"
        return key
    return str(parent_obj) if parent_obj else None


def _format_string_list(items: Any) -> str | None:
    if not items:
        return None
    if isinstance(items, list):
        parts = []
        for item in items:
            if isinstance(item, dict):
                parts.append(item.get("name") or item.get("value") or str(item))
            else:
                parts.append(str(item))
        return ", ".join(parts) if parts else None
    return str(items)


def _format_versions(versions: Any) -> str | None:
    if not versions or not isinstance(versions, list):
        return None
    names = [v.get("name") for v in versions if isinstance(v, dict) and v.get("name")]
    return ", ".join(names) if names else None


def _format_security(sec: Any) -> str | None:
    if isinstance(sec, dict):
        return sec.get("name")
    return str(sec) if sec else None


def _format_environment(env: Any) -> str | None:
    if env is None:
        return None
    if isinstance(env, dict):
        # Cloud ADF — take plain text if nested
        content = env.get("content")
        if isinstance(content, list):
            texts = []
            for block in content:
                if isinstance(block, dict):
                    for inner in block.get("content") or []:
                        if isinstance(inner, dict) and inner.get("text"):
                            texts.append(inner["text"])
            if texts:
                return " ".join(texts)
        return str(env)
    text = str(env).strip()
    return text or None


def _format_issuelinks(links: Any) -> str | None:
    if not links or not isinstance(links, list):
        return None
    parts = []
    for link in links:
        if not isinstance(link, dict):
            continue
        link_type = link.get("type") or {}
        type_name = link_type.get("outward") or link_type.get("inward") or link_type.get("name")
        inward = link.get("inwardIssue") or {}
        outward = link.get("outwardIssue") or {}
        target = outward or inward
        key = target.get("key") if isinstance(target, dict) else None
        if type_name and key:
            parts.append(f"{type_name} {key}")
        elif key:
            parts.append(key)
    return ", ".join(parts) if parts else None


def _format_subtasks(subtasks: Any) -> str | None:
    if not subtasks or not isinstance(subtasks, list):
        return None
    keys = []
    for st in subtasks:
        if isinstance(st, dict):
            key = st.get("key")
            if key:
                keys.append(key)
    return ", ".join(keys) if keys else None


def _format_timetracking(tt: Any) -> str | None:
    if not isinstance(tt, dict):
        return None
    parts = []
    for key, label in (
        ("originalEstimate", "original"),
        ("remainingEstimate", "remaining"),
        ("timeSpent", "spent"),
    ):
        val = tt.get(key)
        if val:
            parts.append(f"{label}: {val}")
    return "; ".join(parts) if parts else None


def _format_seconds(seconds: Any) -> str | None:
    if seconds is None:
        return None
    try:
        total = int(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if total <= 0:
        return "0m"
    hours, rem = divmod(total, 3600)
    minutes = rem // 60
    if hours and minutes:
        return f"{hours}h {minutes}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def _format_progress(progress: Any) -> str | None:
    if not isinstance(progress, dict):
        return None
    prog = progress.get("progress")
    total = progress.get("total")
    if prog is not None and total:
        pct = int((prog / total) * 100) if total else 0
        return f"{pct}%"
    return None


def _format_watches(watches: Any) -> str | None:
    if isinstance(watches, dict):
        count = watches.get("watchCount")
        if count is not None:
            return str(count)
    return None


def _format_status_category(cat: Any) -> str | None:
    if isinstance(cat, dict):
        return cat.get("name") or cat.get("key")
    return str(cat) if cat else None


def _format_sprint_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        formatted = [_format_sprint_value(v) for v in value]
        parts = [p for p in formatted if p]
        return "; ".join(parts) if parts else None
    if isinstance(value, dict):
        name = value.get("name")
        state = value.get("state")
        start = value.get("startDate")
        end = value.get("endDate")
        bits = [str(b) for b in [name, state, start, end] if b]
        return " ".join(bits) if bits else None
    text = str(value)
    match = _SPRINT_NAME_RE.search(text)
    if match:
        return match.group(1).strip()
    if "Sprint" in text or text.startswith("com.atlassian"):
        # GreenHopper encoded string — last segment often readable
        tail = text.rsplit(",", 1)[-1].strip().rstrip("]")
        if tail and not tail.startswith("com."):
            return tail
    return text


def _format_custom_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dict):
        if "value" in value:
            return str(value["value"])
        if "displayName" in value or "emailAddress" in value:
            return _format_person(value)
        if "name" in value:
            return str(value["name"])
        if "key" in value:
            return str(value["key"])
        return str(value)
    if isinstance(value, list):
        parts = [_format_custom_value(v) for v in value]
        joined = ", ".join(p for p in parts if p)
        return joined or None
    return str(value)


def _format_system_field(field_key: str, raw: Any) -> str | None:
    if raw is None:
        return None
    formatters = {
        "status": _format_status,
        "priority": _format_priority,
        "issuetype": _format_issuetype,
        "resolution": _format_resolution,
        "assignee": _format_person,
        "reporter": _format_person,
        "creator": _format_person,
        "project": _format_project,
        "parent": _format_parent,
        "labels": _format_string_list,
        "components": _format_string_list,
        "fixVersions": _format_versions,
        "versions": _format_versions,
        "environment": _format_environment,
        "security": _format_security,
        "issuelinks": _format_issuelinks,
        "subtasks": _format_subtasks,
        "timetracking": _format_timetracking,
        "statusCategory": _format_status_category,
        "watches": _format_watches,
        "progress": _format_progress,
        "aggregateprogress": _format_progress,
        "aggregatetimeoriginalestimate": _format_seconds,
        "aggregatetimeestimate": _format_seconds,
        "aggregatetimespent": _format_seconds,
        "workratio": lambda v: str(v) if v is not None else None,
        "created": lambda v: str(v) if v else None,
        "updated": lambda v: str(v) if v else None,
        "resolutiondate": lambda v: str(v) if v else None,
        "statuscategorychangedate": lambda v: str(v) if v else None,
        "duedate": lambda v: str(v) if v else None,
    }
    fn = formatters.get(field_key)
    if fn:
        return fn(raw)
    text = str(raw).strip()
    return text or None


def _parse_ticket_info_lines(base_context: str) -> dict[str, str]:
    """Parse ``* Label: value`` lines from base context into a label -> line map."""
    label_map: dict[str, str] = {}
    for line in base_context.splitlines():
        stripped = line.strip()
        if not stripped.startswith("* "):
            continue
        body = stripped[2:]
        if ": " in body:
            label, _ = body.split(": ", 1)
            label_map[label.strip()] = stripped
    return label_map


def _rebuild_context(base_context: str, ticket_lines: list[str]) -> str:
    lines = base_context.splitlines()
    out: list[str] = []
    ticket_header_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "Ticket Information:":
            ticket_header_idx = i
            break
    if ticket_header_idx is None:
        out = list(lines)
        if ticket_lines:
            if out and out[-1].strip():
                out.append("")
            out.append("Ticket Information:")
            out.extend(ticket_lines)
        return "\n".join(out)

    out.extend(lines[: ticket_header_idx + 1])
    out.extend(ticket_lines)
    return "\n".join(out)


def format_live_lines(
    issue: dict[str, Any],
    discovered_custom_ids: dict[str, str],
) -> dict[str, str]:
    """Return label -> ``* Label: value`` lines from a Jira issue (no merge yet)."""
    fields = issue.get("fields") or {}
    lines_by_label: dict[str, str] = {}

    def add_field(field_key: str, label: str | None = None) -> None:
        display = label or system_field_label(field_key)
        raw = fields.get(field_key)
        formatted = _format_system_field(field_key, raw)
        if formatted:
            lines_by_label[display] = f"* {display}: {formatted}"

    for field_key in LIVE_SYSTEM_FIELDS:
        add_field(field_key)

    for registry_key, custom_id in discovered_custom_ids.items():
        if registry_key not in CUSTOM_FIELD_SCHEMAS:
            continue
        label = CUSTOM_FIELD_LABELS.get(registry_key, registry_key.replace("_", " ").title())
        formatted = _format_custom_value(fields.get(custom_id))
        if registry_key == "sprint":
            formatted = _format_sprint_value(fields.get(custom_id)) or formatted
        if formatted:
            lines_by_label[label] = f"* {label}: {formatted}"

    return lines_by_label


def merge_enrichment_context(
    base_context: str,
    live_lines_by_label: dict[str, str],
) -> str:
    """Append live-only lines into base context; graph fields are not overwritten."""
    existing = _parse_ticket_info_lines(base_context)

    merged_ticket: dict[str, str] = dict(existing)
    appended: list[str] = []

    for label, line in live_lines_by_label.items():
        if label in merged_ticket:
            continue
        merged_ticket[label] = line
        appended.append(label)

    # Preserve stable order: existing keys first, then new keys
    ordered_labels: list[str] = []
    seen: set[str] = set()
    for line in existing.values():
        lbl = line.split(": ", 1)[0][2:]
        if lbl in merged_ticket and lbl not in seen:
            ordered_labels.append(lbl)
            seen.add(lbl)
    for lbl in merged_ticket:
        if lbl not in seen:
            ordered_labels.append(lbl)
            seen.add(lbl)

    ticket_lines = [merged_ticket[lbl] for lbl in ordered_labels if lbl in merged_ticket]
    return _rebuild_context(base_context, ticket_lines)


def enrich_from_issue(
    base_context: str,
    issue: dict[str, Any],
    discovered_custom_ids: dict[str, str],
) -> str:
    live_lines = format_live_lines(issue, discovered_custom_ids)
    return merge_enrichment_context(base_context, live_lines)
