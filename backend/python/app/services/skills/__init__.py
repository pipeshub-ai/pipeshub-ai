"""Skills management support services (Phase 2/3 of the Custom Skills
Builder plan) — package import (`package_importer.py`) and the pasted-
command parser (`npm_command_parser.py`) that feeds it. Deliberately
separate from `app/agents/agent_loop/skills/` (the agent runtime's own
store/tracker/governor adapters): this package is REST-management-surface
only and has no agent-loop dependency.
"""
