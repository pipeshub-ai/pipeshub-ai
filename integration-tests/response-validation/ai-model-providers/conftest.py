"""Fixtures for the AI-model-provider suite.

``second_user`` creates a real non-admin account rather than reading one from
the environment, so the admin-only checks on these routes run on every run
instead of skipping.
"""

from __future__ import annotations

from helper.second_user import second_user  # noqa: F401 - fixture
