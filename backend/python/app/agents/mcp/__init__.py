"""MCP (Model Context Protocol) client support — registry, auth, and token management.

Phase 1 scope: catalog/registry of well-known MCP servers, org-scoped instances,
per-user/admin credentials (API token, OAuth+DCR, custom headers), and background
OAuth token refresh. Agent-loop tool wiring is Phase 2 (see `wrapper.py`, not present yet).
"""
