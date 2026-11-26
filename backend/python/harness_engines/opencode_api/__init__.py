"""opencode's own server API (``opencode serve``, and ``opencode acp --port``), shared by the engines that run opencode.

The native opencode adapter drives a session through it; the ACP driver's opencode profile asks it whether the tool
gateway is connected (E2E3-F6). Engine adapters never import each other, so what both use lives here.
"""
