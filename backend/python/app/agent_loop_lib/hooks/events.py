"""Every lifecycle point the agent loop dispatches hooks/middleware at.

Each event is backed by exactly one composition primitive in `HookRegistry`
(see `agent_loop.hooks.registry`):

    - "gate"/"observe" events dispatch through a `Pipeline` (see
      `agent_loop.hooks.middleware.pipeline`) paired with a specific context type
      (see `agent_loop.hooks.middleware.context`) — middleware escalates a
      decision (`deny`/`ask`/`block`) rather than raising to short-circuit.
    - PRE_MODEL also dispatches through a `Pipeline`, but as a pure reducer
      (`is_terminal` is always False): every registered shaper runs, in
      registration order, transforming `ctx.messages` in place.
    - PRE_MODEL_CALL is NOT Pipeline-backed — it needs true onion-style
      wrapping (a retry policy calls "the rest of the chain" an arbitrary
      number of times, which a single-pass `next()` continuation can't
      express) — see `HookRegistry.wrapper()` / `agent_loop.hooks.middleware.wrapper.Wrapper`.

| Event              | Context type            | Composition | Fires                                    |
|---------------------|--------------------------|-------------|-------------------------------------------|
| PRE_TOOL_USE         | ToolCallContext          | Pipeline    | before a tool executes                    |
| POST_TOOL_USE        | ToolResultContext        | Pipeline    | after a tool executes                     |
| PRE_AGENT            | AgentLifecycleContext    | Pipeline    | once, before the first turn               |
| POST_AGENT           | AgentLifecycleContext    | Pipeline    | once, after the run produces AgentResult  |
| PRE_TURN             | TurnContext              | Pipeline    | before each turn's model call             |
| POST_TURN            | TurnContext              | Pipeline    | after each turn completes                 |
| PRE_MODEL            | ModelCallContext         | Pipeline (reducer) | shaping messages before the LLM call |
| PRE_MODEL_CALL       | (none — Wrapper)         | Wrapper     | wraps the actual LLM call (retry/failover)|
| POST_MODEL           | ModelResponseContext     | Pipeline (reducer) | observing the model's raw response, before tool calls run |
| GUARDRAIL_INPUT      | GuardrailContext         | Pipeline    | checking pending input before the model   |
| GUARDRAIL_OUTPUT     | GuardrailContext         | Pipeline    | checking the model's proposed final answer|

POST_MODEL is a pure reducer like PRE_MODEL (no deny/ask decision) — it lets
deterministic recovery policy (e.g. truncated-response handling, see
`hooks.builtin.truncation_recovery`) live as composable middleware instead of
inline in the turn loop, while the turn loop itself keeps owning whether to
short-circuit the rest of the turn.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["HookEvent"]


class HookEvent(str, Enum):
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    PRE_AGENT = "pre_agent"
    POST_AGENT = "post_agent"
    PRE_TURN = "pre_turn"
    POST_TURN = "post_turn"
    PRE_MODEL = "pre_model"
    PRE_MODEL_CALL = "pre_model_call"
    POST_MODEL = "post_model"
    GUARDRAIL_INPUT = "guardrail_input"
    GUARDRAIL_OUTPUT = "guardrail_output"
