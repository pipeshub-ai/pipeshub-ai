"""Adapter layer bridging PipesHub to the `agent-loop` framework
(`app/agent_loop_lib`, imported as the `app.agent_loop_lib` package).

This is the *only* place PipesHub-specific integration code for the
agent-loop migration lives — `app.agent_loop_lib` (the inlined library)
itself is treated as unmodified, read-only third-party code. Everything
here composes agent-loop's public interfaces (`Agent`, `AgentSpec`,
`AgentRuntime`, `LLMTransport`, `Tool`, `SystemPromptBuilder`,
`HookRegistry`, ...) with PipesHub's existing services (LangChain models,
`StructuredTool` actions, GraphDB/Blob storage, citation pipeline, SSE
streaming) rather than subclassing or forking agent-loop.

Populated across the migration phases:
  - Phase 2: `converters.py`, `langchain_transport.py` — LLMTransport adapter
  - Phase 3: `context.py`, `tool_adapter.py`, `tool_loader.py`,
    `tool_guidance.py` — tool adapter layer
  - Phase 4: `prompt_builder.py` — SystemPromptBuilder implementation
  - Phase 5: `hooks.py` — PRE/POST_TOOL_USE middleware
  - Phase 6: `respond_pipeline.py` — post-agent response synthesis
  - Phase 7: `factory.py`, `router.py`, `sse_emitter.py` — wiring + SSE bridge
  - Phase 8: `stream_bridge.py` — route-layer SSE event bridge
"""
