"""Claude Code adapter: launch renderer, stream-json session driver over stdio, event mapper and transcript state codec.

Claude Code is proprietary and never shipped by PipesHub (D29): the customer's admin supplies the binary,
which runs only when its digest is on the allowlist.
"""

from harness_engines.claude_code.adapter import ClaudeCodeAdapter

__all__ = ["ClaudeCodeAdapter"]
