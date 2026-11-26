# pyright: ignore-file

"""Zoom connector fixtures: the zoom_sim suite syncs from the Zoom simulator; its fixtures are in sim_world.py
(ZOOM_SIM_ITS.md). There is no live Zoom suite: Zoom offers no account a test may write to."""

from connectors.zoom.sim_world import (  # noqa: F401 - fixtures of the zoom_sim suite
    zoom_front,
    zoom_sim,
    zoom_sim_people,
    zoom_world,
)
