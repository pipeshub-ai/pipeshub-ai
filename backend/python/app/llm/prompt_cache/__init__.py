"""Framework-neutral prompt-caching support.

This package has no dependency on `agent_loop_lib` or on any specific
call site — it is imported by the agent loop adapter
(`app.agents.agent_loop`), and, from Phase 8 onward, by the query and
indexing call sites directly. `agent_loop_lib` itself depends only on
the `PromptCacheStrategy` Protocol declared in
`app.agent_loop_lib.cache.base` and never imports this package, so the
library stays runnable standalone (see the plan's "Why three layers
instead of one package").

Phase 0 ships only `metrics`: pure observation of cache usage already
returned by providers, with no behavior change. Later phases add
`capabilities`, `decision`, `config`, `allocator`, `standdown`,
`strategy/*`, and `factory`.
"""
