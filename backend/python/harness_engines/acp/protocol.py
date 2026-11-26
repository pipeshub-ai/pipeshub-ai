"""The Agent Client Protocol (ACP) as this driver speaks it: the pinned version, method names, codes and bounds.

ACP is Zed's open protocol between a client (here: the harness) and a coding agent: JSON-RPC 2.0, one object per
line over the agent's stdin and stdout. The schema this driver was written against is
``agentclientprotocol/agent-client-protocol`` tag ``schema-v1.26.0``, ``schema/v1/schema.json`` (sha256 below),
protocol version 1. Only the stable surface is used; the one unstable field read is ``PromptResponse.usage``, which
is optional and ignored when absent.
"""

from __future__ import annotations

from typing import Final

PROTOCOL_VERSION: Final = 1
SCHEMA_RELEASE: Final = "schema-v1.26.0"
SCHEMA_SHA256: Final = "4bcd1d2051ff7ed379bd9b234fb9b09c5ab9245478698e9fab805bd9b52a33a6"
JSONRPC: Final = "2.0"

# Agent methods (client → agent).
INITIALIZE: Final = "initialize"
SESSION_NEW: Final = "session/new"
SESSION_LOAD: Final = "session/load"
# Resumes a session without replaying it (``sessionCapabilities.resume``, stable in this schema): DeepSeek Harness
# offers it in place of ``session/load`` (WP-105).
SESSION_RESUME: Final = "session/resume"
SESSION_PROMPT: Final = "session/prompt"
SESSION_CANCEL: Final = "session/cancel"
SESSION_SET_CONFIG: Final = "session/set_config_option"
# Client methods (agent → client).
SESSION_UPDATE: Final = "session/update"
REQUEST_PERMISSION: Final = "session/request_permission"
FS_READ: Final = "fs/read_text_file"
FS_WRITE: Final = "fs/write_text_file"
TERMINAL_PREFIX: Final = "terminal/"

# JSON-RPC and ACP error codes (``ErrorCode``).
PARSE_ERROR: Final = -32700
INVALID_REQUEST: Final = -32600
METHOD_NOT_FOUND: Final = -32601
INVALID_PARAMS: Final = -32602
INTERNAL_ERROR: Final = -32603
REQUEST_CANCELLED: Final = -32800
RESOURCE_NOT_FOUND: Final = -32002

# One line of the agent's output. Larger is not ACP traffic a coding turn needs (a whole file travels through
# fs/write_text_file, bounded below), and an unbounded line would be unbounded memory in the worker.
MAX_LINE_BYTES: Final = 8 * 1024 * 1024
# What fs/read_text_file and fs/write_text_file move in one call.
MAX_FS_BYTES: Final = 4 * 1024 * 1024
# Messages that are not JSON-RPC 2.0 (bad JSON, no ``jsonrpc``, an id of the wrong type) a session tolerates
# before it treats the agent as broken.
MAX_INVALID_MESSAGES: Final = 64

__all__ = [
    "FS_READ",
    "FS_WRITE",
    "INITIALIZE",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JSONRPC",
    "MAX_FS_BYTES",
    "MAX_INVALID_MESSAGES",
    "MAX_LINE_BYTES",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "REQUEST_CANCELLED",
    "REQUEST_PERMISSION",
    "RESOURCE_NOT_FOUND",
    "SCHEMA_RELEASE",
    "SCHEMA_SHA256",
    "SESSION_CANCEL",
    "SESSION_LOAD",
    "SESSION_NEW",
    "SESSION_PROMPT",
    "SESSION_RESUME",
    "SESSION_SET_CONFIG",
    "SESSION_UPDATE",
    "TERMINAL_PREFIX",
]
