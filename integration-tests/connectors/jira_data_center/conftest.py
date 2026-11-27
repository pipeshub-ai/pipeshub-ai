# pyright: ignore-file

"""Jira Data Center connector fixtures: the ``jira_dc_sim`` suite syncs from the Jira Data Center simulator; its
fixtures are in ``sim_world.py`` (JIRA_DC_SIM_ITS.md)."""

from connectors.jira_data_center.sim_world import (  # noqa: F401 - fixtures of the jira_dc_sim suite
    jira_dc_sim,
    jira_dc_sim_people,
    jira_dc_world,
)
