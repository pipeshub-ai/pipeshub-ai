# pyright: ignore-file

"""Drupal Wiki connector fixtures.

The suite syncs from the Drupal Wiki simulator (``drupal-wiki-sim``, compose override
docker-compose.integration.drupal-wiki-sim.yml); its fixtures are in ``sim_world.py`` (DRUPAL_WIKI_SIM_ITS.md).
"""

from connectors.drupal_wiki.sim_world import (  # noqa: F401 - fixtures of the drupal_wiki_sim suite
    drupal_wiki_sim,
    drupal_wiki_sim_people,
    drupal_world,
)
