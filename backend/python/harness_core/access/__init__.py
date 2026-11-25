"""Model access (ARCHITECTURE §4.6): types, the run's model grant, route resolution, spend.

Ports: ``RunModelGrants``, ``ModelAccessResolver``, ``SpendCounter``. In-memory
adapters in ``harness_core.access.memory``; contract suites in
``harness_core.testing.contracts.model_access``. The chat-completions gateway
that uses them is ``harness_core.gateway.model_gateway``.
"""
