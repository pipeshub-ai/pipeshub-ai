"""This process's region (OWD-12), read through the ``RegionPort`` extension point (``default`` in this build), and
where a model route's calls are served, read through ``ModelRegions`` (no region, no pin in this build)."""

from __future__ import annotations

import os

from app.utils.model_placement import model_regions
from harness_core.region import SINGLE_REGION, RegionPort


def deployment_region() -> str:
    """The region this process runs in."""
    # Imported here: the edition module pulls in the connector services, which the journal client and the
    # sandbox broker do not otherwise load.
    from app.edition_services import harness_edition

    return harness_edition().port(RegionPort, SINGLE_REGION).region(os.environ)


__all__ = ["deployment_region", "model_regions"]
