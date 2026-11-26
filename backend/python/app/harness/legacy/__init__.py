"""Today's loop assembled from what Node resolved for the turn (CPD-10).

Under ``CONTROL_PLANE_PIN=on`` Node signs the turn's resolution into ``X-PipesHub-Resolved`` and proxies a legacy
turn as before. The query service verifies it (``resolved_turn``) and reads from it what it would otherwise read
per request: the Labs stages, the flow rules, whether the caller may run the agent, and which installations hold
a binding (``resolved_request``). A request without the header reads as before; one whose header does not verify
is refused, never served from those reads.
"""
