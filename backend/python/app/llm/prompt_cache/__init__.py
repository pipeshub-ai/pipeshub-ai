"""Framework-neutral prompt-caching support.

This package has no dependency on `agent_loop_lib` or on any specific
call site — it is imported by the agent loop adapter
(`app.agents.agent_loop`) and by query and indexing call sites
directly. `agent_loop_lib` itself depends only on the
`PromptCacheStrategy` Protocol declared in
`app.agent_loop_lib.cache.base` and never imports this package, so the
library stays runnable standalone (see the plan's "Why three layers
instead of one package").

Contents: `metrics` (observation of provider-reported cache usage),
`capabilities`, `decision`, `config`, `allocator`, `cache_key`,
`standdown`, `ttl`, `langchain_kwargs`, `strategy/*`, and `factory`.
"""
