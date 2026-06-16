"""Curated Jira issue fields for live LLM enrichment — edit this file to add/remove fields."""

from __future__ import annotations

from typing import Any

# Fetched live at query time (graph already has status, priority, assignee, etc.).
LIVE_SYSTEM_FIELDS: list[str] = [
    "resolution",
    "resolutiondate",
    "duedate",
    "labels",
    "components",
    "fixVersions",
    "versions",
    "environment",
    "security",
    "project",
    "parent",
    "issuelinks",
    "subtasks",
    "created",
    "timetracking",
    "aggregatetimeoriginalestimate",
    "aggregatetimeestimate",
    "aggregatetimespent",
    "watches",
]

# Registry key -> schema.custom (or matcher dict for ambiguous types like Story Points).
CUSTOM_FIELD_SCHEMAS: dict[str, str | list[Any]] = {
    "sprint": "com.pyxis.greenhopper.jira:gh-sprint",
    "epic_link": "com.pyxis.greenhopper.jira:gh-epic-link",
    "story_points": [
        "com.pyxis.greenhopper.jira:jsw-story-points",
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:float",
            "name_in": ("Story Points", "Story point estimate"),
        },
    ],
    "original_story_points": "com.atlassian.jpo:jpo-custom-field-original-story-points",
    "epic_name": "com.pyxis.greenhopper.jira:gh-epic-label",
    "epic_status": "com.pyxis.greenhopper.jira:gh-epic-status",
    "team": "com.atlassian.teams:rm-teams-custom-field-team",
    "target_start": "com.atlassian.jpo:jpo-custom-field-baseline-start",
    "target_end": "com.atlassian.jpo:jpo-custom-field-baseline-end",
    "parent_link": "com.atlassian.jpo:jpo-custom-field-parent",
    "severity": [
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:select",
            "name_in": ("Severity",),
        },
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:radiobuttons",
            "name_in": ("Severity",),
        },
    ],
    "impact": [
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:select",
            "name_in": ("Impact",),
        },
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:radiobuttons",
            "name_in": ("Impact",),
        },
    ],
    "urgency": [
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:select",
            "name_in": ("Urgency",),
        },
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:radiobuttons",
            "name_in": ("Urgency",),
        },
    ],
    "start_date": [
        "com.atlassian.jpo:jpo-custom-field-start",
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:datepicker",
            "name_in": ("Start date", "Start Date"),
        },
    ],
    "approvers": [
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:multiuserpicker",
            "name_in": ("Approvers",),
        },
        {
            "schema": "com.atlassian.jira.plugin.system.customfieldtypes:userpicker",
            "name_in": ("Approver",),
        },
    ],
}

# Human-readable labels for registry keys in LLM output.
CUSTOM_FIELD_LABELS: dict[str, str] = {
    "sprint": "Sprint",
    "epic_link": "Epic Link",
    "story_points": "Story Points",
    "original_story_points": "Original Story Points",
    "epic_name": "Epic Name",
    "epic_status": "Epic Status",
    "team": "Team",
    "target_start": "Target Start",
    "target_end": "Target End",
    "parent_link": "Parent Link",
    "severity": "Severity",
    "impact": "Impact",
    "urgency": "Urgency",
    "start_date": "Start Date",
    "approvers": "Approvers",
}

SKIPPED_CUSTOM_SCHEMAS: set[str] = {
    "com.pyxis.greenhopper.jira:gh-lexo-rank",
    "com.pyxis.greenhopper.jira:gh-epic-color",
}

EXCLUDED_SYSTEM_FIELDS: set[str] = {
    "description",
    "comment",
    "worklog",
    "issuekey",
    "lastViewed",
    "thumbnail",
    "votes",
    "attachment",
    "timeoriginalestimate",
    "timeestimate",
    "timespent",
    "progress",
    "aggregateprogress",
    "workratio",
    "statusCategory",
    "statuscategorychangedate",
}

# Map system field keys to LLM line labels (when different from title-case key).
SYSTEM_FIELD_LABELS: dict[str, str] = {
    "issuetype": "Type",
    "fixVersions": "Fix Versions",
    "versions": "Affects Versions",
    "issuelinks": "Linked Issues",
    "subtasks": "Sub-tasks",
    "resolutiondate": "Resolved",
    "resolution": "Resolution",
    "duedate": "Due Date",
    "aggregatetimeoriginalestimate": "Aggregate Original Estimate",
    "aggregatetimeestimate": "Aggregate Remaining Estimate",
    "aggregatetimespent": "Aggregate Time Spent",
    "timetracking": "Time Tracking",
}


def build_search_field_list(discovered_custom_ids: dict[str, str]) -> list[str]:
    """Build explicit ``fields`` list for Jira search (never ``*all``)."""
    fields: list[str] = []
    seen: set[str] = set()
    for key in LIVE_SYSTEM_FIELDS + list(discovered_custom_ids.values()):
        if key in EXCLUDED_SYSTEM_FIELDS or key in seen:
            continue
        seen.add(key)
        fields.append(key)
    return fields


def system_field_label(field_key: str) -> str:
    if field_key in SYSTEM_FIELD_LABELS:
        return SYSTEM_FIELD_LABELS[field_key]
    if field_key in LIVE_SYSTEM_FIELDS:
        return field_key.replace("_", " ").title()
    return field_key
