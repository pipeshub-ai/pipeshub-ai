"""Admin alerts when code runs in chat keep waiting for a sandbox, or are refused.

The query workers record their runs (``agent_loop_lib.sandbox.load_ledger``).
One worker per container (``AlertLeader``) sums them over a window
(``read_window``), decides (``evaluate``), claims a cooldown in the
configuration store (``AlertCooldown``) and publishes one event on the
``notification`` topic for the installation's admins (``build_alert_event``).
``run_sandbox_alert_loop`` ties them together.
"""
