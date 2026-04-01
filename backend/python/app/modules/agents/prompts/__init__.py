"""
Agent prompt templates and guidance.

This module contains all the prompt strings used by the agent system,
extracted from the monolithic nodes.py for better maintainability.
"""

from app.modules.agents.prompts.planner_prompts import PLANNER_SYSTEM_PROMPT
from app.modules.agents.prompts.reflect_prompts import REFLECT_PROMPT
from app.modules.agents.prompts.tool_guidance import (
    CLICKUP_GUIDANCE,
    CONFLUENCE_GUIDANCE,
    GITHUB_GUIDANCE,
    JIRA_GUIDANCE,
    MARIADB_GUIDANCE,
    ONEDRIVE_GUIDANCE,
    OUTLOOK_GUIDANCE,
    REDSHIFT_GUIDANCE,
    SLACK_GUIDANCE,
    TEAMS_GUIDANCE,
    ZOOM_GUIDANCE,
)

__all__ = [
    "PLANNER_SYSTEM_PROMPT",
    "REFLECT_PROMPT",
    "CLICKUP_GUIDANCE",
    "CONFLUENCE_GUIDANCE",
    "GITHUB_GUIDANCE",
    "JIRA_GUIDANCE",
    "MARIADB_GUIDANCE",
    "ONEDRIVE_GUIDANCE",
    "OUTLOOK_GUIDANCE",
    "REDSHIFT_GUIDANCE",
    "SLACK_GUIDANCE",
    "TEAMS_GUIDANCE",
    "ZOOM_GUIDANCE",
]
