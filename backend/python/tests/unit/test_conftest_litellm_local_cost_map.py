"""No test downloads LiteLLM's model price map (tests/conftest.py sets LITELLM_LOCAL_MODEL_COST_MAP).

LiteLLM reads the price map from raw.githubusercontent.com on import unless the switch is set; a unit or integration run
then depends on the network and sends a request from every worker.
"""

import os
import subprocess
import sys
import textwrap

import httpx
import pytest
from litellm.litellm_core_utils.get_model_cost_map import get_model_cost_map


def test_the_run_uses_the_price_map_litellm_ships() -> None:
    assert os.environ.get("LITELLM_LOCAL_MODEL_COST_MAP") == "True"


def test_loading_the_price_map_makes_no_request(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[str] = []

    def get(url: str, *_args: object, **_kwargs: object) -> httpx.Response:
        requests.append(url)
        raise httpx.ConnectError("no network in tests")

    monkeypatch.setattr(httpx, "get", get)

    model_cost = get_model_cost_map(url="https://example.invalid/model_prices.json")

    assert requests == []
    assert model_cost  # the shipped copy, not an empty fallback


def test_importing_litellm_in_a_worker_opens_no_connection() -> None:
    """A fresh interpreter with the run's environment: any socket connection while litellm loads fails the test."""
    script = textwrap.dedent(
        """
        import socket

        attempts = []

        def refuse(*args, **kwargs):
            # Recorded first: litellm catches the exception of a failed fetch and falls back to its shipped copy.
            attempts.append(args[:2])
            raise AssertionError(f"network connection attempted: {args[:2]}")

        socket.socket.connect = refuse
        socket.create_connection = refuse
        socket.getaddrinfo = refuse

        import litellm

        assert litellm.model_cost, "no price map"
        assert not attempts, f"network connection attempted: {attempts}"
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120, env=dict(os.environ), check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
