# Prompt Caching

How PipesHub reuses cached LLM prompt prefixes to cut latency and cost, why it
is designed the way it is, and what to do to extend it to a new call site or
provider. The implementation lives almost entirely under
[`backend/python/app/llm/prompt_cache/`](../backend/python/app/llm/prompt_cache/),
with the two production call sites in
[`app/agents/agent_loop/langchain_transport.py`](../backend/python/app/agents/agent_loop/langchain_transport.py)
(chat/agent-loop traffic) and
[`app/utils/streaming.py`](../backend/python/app/utils/streaming.py) (indexing
structured-output calls).

---

## 1. The core idea: cache by reuse class, not by a global switch

The question "how do we make sure caching doesn't stay on forever, silently
accumulating cost?" has a precise answer, and it isn't a TTL and isn't a kill
switch. Every provider's cache entries expire on their own (5m–30m; there is
no persistent cache storage and no per-entry storage fee on any path PipesHub
uses). The actual cost risk runs the other way: **paying a write premium on a
prefix that is never read again before it expires.**

So the unit of decision isn't "is caching on globally" — it's **"will this
specific call's prefix be re-read before the TTL expires?"**, answered per
call site via `CacheReuseClass` (`app/llm/prompt_cache/decision.py`):

| Reuse class | Meaning | Default |
|---|---|---|
| `MULTI_TURN` | The agent-loop turn sequence — the same system/tool prefix is re-read on every subsequent turn, typically within seconds. Break-even clears on turn 2. | **On** |
| `SHARED_STATIC` | A prefix that's byte-identical across *different* requests (e.g. an indexing prompt's instruction block, reused across every document for an org). Payoff depends on request volume, not turns — deployment-dependent. | **Off**, enabled per call site only once Phase-0 measurement justifies it |
| `ONE_SHOT_UNIQUE` | The prefix contains content unique to this one call (a single document body, a health-check probe). Structurally guaranteed write-with-zero-reads. | **Never cached** |

`decide()` is the single function that turns `(reuse_class, provider, model,
CacheConfig)` into a `CacheDecision(enabled, reason, ...)`. Every `reason`
string is a stable, logged value — see §5.

## 2. Layered configuration (`ENABLE_PROMPT_CACHING`)

Caching is **enabled by default** and can be disabled two ways, checked in
this order by `resolve_cache_config()` (`app/llm/prompt_cache/config.py`):

1. **Environment variable floor** — `ENABLE_PROMPT_CACHING=false` on the
   Python query/indexing/connector process. This is a hard floor: no platform
   flag can override it back on. Leave unset (or `true`) to keep the floor
   open.
2. **Platform feature flag** (`ENABLE_PROMPT_CACHING` in Labs, wired through
   `backend/nodejs/apps/.../configuration_manager/constants.ts` and consumed
   in Python via `FeatureFlagService`) — an admin-facing toggle layered *on
   top of* the env floor. `enabled` is the AND of both.

`FeatureFlagService` only reads its already-in-memory value when resolving
cache kwargs (never an etcd round-trip on the hot path); a background task
started by `FeatureFlagService.start_periodic_refresh()` keeps that in-memory
value current every 60s in both the query and indexing processes.

Turning the flag off does not, by itself, retroactively enable any
`SHARED_STATIC` site that hasn't been individually justified — see §1's table.

## 3. Per-provider support matrix

Resolved by `resolve_capability(provider, model)`
(`app/llm/prompt_cache/capabilities.py`) — the single place that maps a
`(provider, model)` pair to a `CacheCapability` (mode, minimum prefix length,
TTL, breakpoint cap, cost multipliers). Thresholds are the provider's
publicly documented figures at time of writing.

| Provider | Mode | How it's invoked on the LangChain path | Notes |
|---|---|---|---|
| Anthropic | Explicit (auto-placed) | `cache_control={"type": "ephemeral"}` invoke kwarg — places the breakpoint on the last cacheable block, no message restructuring needed | 1,024-token floor (Sonnet/Opus), 2,048 (Haiku); 5m default TTL |
| OpenAI (incl. Azure) | Automatic (GPT-4o+) / Explicit (GPT-5.6+) | `prompt_cache_key` invoke kwarg (tenant+user-scoped routing hint) | 1,024-token floor; explicit breakpoints (`prompt_cache_breakpoint`) are **not** sent on the LangChain path — see §4 |
| Google (Gemini 2.x/3.x) | Automatic, implicit | Nothing — no parameter exists | Google may discount without ever reporting `cache_read` — a 0% measured hit rate is not proof caching failed |
| Bedrock | Unresolved | N/A — resolves to `mode="none"` | See §6, Open items |
| Everything else (Ollama, Mistral, Groq, ...) | `none` | N/A | Conservative default; never guesses a shape the API might reject |

`resolve_cache_provider()` additionally downgrades a LangChain-detected
`"openai"` label to `"unknown"` (i.e. no caching) whenever the `ChatOpenAI`
instance's base URL isn't actually `api.openai.com` — protects OpenAI-
compatible gateways (LM Studio, LiteLLM proxy, OpenRouter, MiniMax) from
receiving an OpenAI-only kwarg their backend might 400 on.

## 4. Two independent implementations, on purpose

There are two separate places caching logic lives, and they deliberately
don't share machinery:

- **`app/agent_loop_lib/cache/` + `app/llm/prompt_cache/strategy/`** — the
  `PromptCacheStrategy` protocol and its `AnthropicCacheStrategy`/
  `OpenAICacheStrategy` implementations, injected into the native
  `AnthropicTransport`/`OpenAITransport` (`app/agent_loop_lib/transport/`).
  These operate on the **formatted provider request dict** each transport
  builds itself, so they can do full block-list restructuring: a
  `BreakpointAllocator`-governed budget, a cumulative-prefix floor check
  before spending a breakpoint, and (Anthropic) two advancing message
  breakpoints. This is the richer implementation, but `agent_loop_lib`'s
  native transports are not PipesHub's production chat path (see below).
- **`app/llm/prompt_cache/langchain_kwargs.py`** — a much simpler
  invoke-kwarg resolver for `LangChainTransport`, which is the path that
  carries **all real PipesHub chat/agent-loop traffic** (every provider is
  already wired through LangChain's `BaseChatModel`; see
  `langchain_transport.py`'s module docstring). `LangChainTransport` never
  sees the raw provider request dict — LangChain builds that internally —
  so there is no block-list to restructure. Anthropic's automatic
  `cache_control` invoke kwarg and OpenAI's `prompt_cache_key` cover this
  path's needs without it.

  **`prompt_cache_options`/explicit per-block breakpoints are deliberately
  never sent on the LangChain path.** Setting `prompt_cache_options.mode=
  "explicit"` with zero breakpoints placed would disable OpenAI's implicit
  last-message caching outright — turning "explicit mode" into strictly
  worse caching than doing nothing. That restructuring only exists today for
  the native `OpenAITransport` seam.

If you're adding a new call site, use whichever of these your call goes
through — almost everything should go through `LangChainTransport`.

## 5. Observability: one log line correlates decision and outcome

Every instrumented call emits exactly one `prompt_cache_usage` log line (via
`app/llm/prompt_cache/metrics.py::log_cache_usage`) carrying:

- `provider`, `model`, `call_site` — where this call came from.
- `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens` —
  what actually happened. `input_tokens` **excludes** cached tokens on every
  path (fixed a double-count bug in the LangChain converter — see
  `app/agents/agent_loop/converters.py::token_usage_from_ai_message`).
- `decision_enabled`, `decision_reason` — *why* caching was or wasn't
  attempted, resolved right before the call and threaded through so a reader
  never has to cross-reference two separately-timed log lines.
- `net_savings_tokens` — this call's cache activity converted to
  full-price-input-token equivalents (not dollars); negative on a cold write
  is expected — the payoff is a *later* call's reads, not this one.

`decision_reason` is always one of a small set of stable strings, useful for
dashboards/alerting:

| Reason | Meaning |
|---|---|
| `multi_turn_default_on` | Caching attempted — normal `MULTI_TURN` case |
| `shared_static_enabled_for_call_site` | Caching attempted — an explicitly-opted-in `SHARED_STATIC` site |
| `shared_static_off_by_default` | `SHARED_STATIC` call site, not yet opted in |
| `one_shot_unique_never_cached` | This reuse class is never cached, by design |
| `cache_disabled_by_env` / `cache_disabled_by_feature_flag` | The kill switch (§2) is off |
| `capability_mode_none` | Unsupported provider, or an unrecognized/legacy model on a known provider |

## 6. Query and indexing call sites (Phase 8 verdicts)

`SHARED_STATIC` starts OFF everywhere; a call site earns it only once its
prompt shape and expected traffic clear the model's floor and would actually
be re-read within the TTL. Verdicts as analyzed (structural, pre-Phase-0-data):

- **`app/modules/transformers/document_extraction.py`** (`classify`,
  `extract_metadata`) — wired for `SHARED_STATIC` with an org-scoped cache
  key (`document_extraction:{org_id}`), **kept off** pending Phase 0 data.
  Its static instruction block (~700–850 tokens after substitution) is the
  most plausible candidate of the sites reviewed, but flipping it on for
  Anthropic specifically needs care: the static instructions are the FIRST
  block(s) of a single multi-block `HumanMessage`, with the unique document
  content LAST. Anthropic's automatic `cache_control` invoke kwarg marks the
  LAST block — flipping `shared_static_enabled=True` today would cache the
  unique document, not the shared instructions (a guaranteed write with zero
  reads). Safe to flip for OpenAI/Gemini-only deployments today (their
  automatic modes match a byte-prefix, not a block position).
- **`app/utils/query_transform.py`** — static prefix (~35–40 tokens) is far
  below every provider's floor. Not worth it.
- **`app/utils/query_decompose.py`** — static prefix is large enough
  (~700–850 tokens) but the user's `{query}` is embedded too early in the
  text; would need prompt reordering to be cache-effective. Deferred until
  measurement justifies the reorder.
- **`app/utils/table_enrichment.py`** — inputs are highly variable per-table
  (`ONE_SHOT_UNIQUE`). Not a candidate.

Recording "not worth it" for a site is a valid, useful outcome — the goal is
never to maximize the number of cached call sites.

## 7. Extending this to a new call site

1. Decide the `CacheReuseClass` (§1) — most new agent-loop call sites are
   `MULTI_TURN` and need no code changes (already on by default via
   `LangChainTransport`). A one-shot call (health checks, `IntentParser`) is
   `ONE_SHOT_UNIQUE` — also nothing to do, it's already excluded.
2. For a genuinely reusable static prefix across *different* requests, pass
   `reuse_class=CacheReuseClass.SHARED_STATIC` and a stable `cache_key`
   scoped to whatever dimension the prefix is actually shared across (org,
   org+user, etc. — see `build_prompt_cache_key`'s docstring for why the
   agent loop's stable band needs both org AND user). Leave
   `shared_static_enabled=False` until Phase 0 log data
   (`prompt_cache_usage`, `decision_reason=shared_static_off_by_default`)
   shows the prefix clears the model's floor and is re-read within the TTL
   at real request rates.
3. Never hand-roll a provider-specific kwarg at the call site — go through
   `resolve_langchain_cache_kwargs` (LangChain path) or a
   `PromptCacheStrategy` (native transport path, §4).

## 8. Open items (Phase 9)

- **Bedrock — spiked, not wired.** Source-inspected against the pinned
  `langchain-aws==1.1.0` tag (`pyproject.toml`) rather than left as a guess:
  - AWS's Bedrock InvokeModel API genuinely honors `cache_control` on Claude
    models — [confirmed in AWS's own docs](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html),
    exact wire format `{"cache_control": {"type": "ephemeral"}}`, same as
    Anthropic's direct API.
  - `ChatBedrock._format_anthropic_messages` (langchain-aws 1.1.0) *does*
    forward `cache_control` on regular message content blocks to that wire
    format.
  - But **system-prompt `cache_control` is silently dropped** at 1.1.0 —
    system content gets flattened to a plain string before caching survives
    ([langchain-aws#793](https://github.com/langchain-ai/langchain-aws/issues/793),
    fixed by [langchain-aws#838](https://github.com/langchain-ai/langchain-aws/pull/838)).
    That's the highest-value target (mirrors the agent loop's stable band),
    so losing it defeats most of the point of wiring this up.
  - The `cache_control=...` **invoke-kwarg** convenience this repo's
    `resolve_langchain_cache_kwargs` relies on for `ChatAnthropic` was only
    *added* to `ChatBedrock` in **langchain-aws 1.4.0**
    ([#838](https://github.com/langchain-ai/langchain-aws/pull/838)/[#839](https://github.com/langchain-ai/langchain-aws/pull/839),
    merged 2026-03-04) — it doesn't exist at the pinned 1.1.0, so sending it
    today would be a no-op.

  `capabilities.py` resolves Bedrock to `mode="none"` (no cache kwargs sent)
  until either: (a) `langchain-aws` is bumped to `>=1.4.0` and this file's
  invoke-kwarg mechanism gets a Bedrock case added and validated against a
  live endpoint, or (b) PipesHub migrates from `ChatBedrock` to
  `ChatBedrockConverse` — which a langchain-aws maintainer, [on the fix
  PR](https://github.com/langchain-ai/langchain-aws/pull/838), said the team
  is "actively pushing users to migrate to" over `ChatBedrock` in general,
  and which has never had this bug. Both are dependency/migration decisions
  outside this plan's scope.
- **No 1h TTL in v1.** Anthropic's extended 1h TTL needs roughly 11 reads to
  break even and only pays off for agent runs that pause for human review.
  `CacheCapability.extended_ttl` models the option so it exists, but no call
  site selects it and no config surface exposes it until a workload clearly
  needs it — keeps the TTL-ordering constraint (1h blocks must precede 5m
  blocks) out of v1 entirely.

## 9. Known upstream caveats (not PipesHub bugs)

- `cache_control` on a `tool_use` block is silently dropped by
  `langchain-anthropic` when `tool_calls` is also set
  ([langchain#38398](https://github.com/langchain-ai/langchain/issues/38398),
  open). The agent loop always has tool calls in play; prefer system/tool-
  schema breakpoints over assistant-block ones.
- `cache_control` inside a multi-block `ToolMessage.content[]` returns
  Anthropic's `invalid_cache` 400
  ([langchain#34920](https://github.com/langchain-ai/langchain/issues/34920),
  open). The marker must sit on the `tool_result` root, not a sub-block —
  the native `AnthropicTransport` already does this correctly.
- Google's `cached_content_token_count` only covers *full* cache hits — a 0%
  measured `cache_read` rate on Gemini does not mean caching is broken.

## 10. Tests

- Unit: `backend/python/tests/unit/llm/prompt_cache/`,
  `backend/python/tests/unit/agent_loop_lib/cache/`.
- LangChain integration seam:
  `backend/python/tests/unit/agents/adapter/test_langchain_transport.py`
  (`TestCacheDecisionOutcomeCorrelation`).
- Native transport seams:
  `backend/python/tests/unit/agent_loop_lib/transport/test_anthropic_cache_strategy_seam.py`,
  `test_openai_cache_strategy_seam.py`.
- Prefix-stability regression (the single highest-value test — a broken
  stable-block build silently turns caching into a pure cost increase):
  `backend/python/tests/unit/agents/adapter/test_prompt_invariants.py`.
- E2E (multi-turn, scripted providers, no network):
  `backend/python/tests/integration/test_prompt_caching_e2e.py`.
