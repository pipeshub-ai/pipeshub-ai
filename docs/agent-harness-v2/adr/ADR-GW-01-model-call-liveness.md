# ADR-GW-01: A model call stays alive while the model works, and ends when its caller is gone

- Extends: ADR-OWD-08 (model gateway: engines reach models only through it), ADR-OWD-03 (the egress proxy is a sandbox's only way out).
- Related: ADR-RUN-01 D4 (a turn's working time pauses while a person decides), WP-65 and WP-66 (a cut call is not drained; it is charged an estimate).
- Found by: WP-127 (a manual test in which a run's limit kept being breached).

## Context

A coding engine's model call crosses two hops: sandbox → egress proxy → Model Gateway → provider. Each hop had its own idea of time, and none of them agreed:

| Hop | What it did with a long call |
|---|---|
| Egress proxy | Cuts an upstream that sends nothing for `UPSTREAM_READ_TIMEOUT_S` (300 s). A safety net for a dead upstream. |
| Model Gateway, streamed | Sent a frame only when the wire had one. The Messages wire sends a tool call whole from the final response, and hides thinking unless asked, so a model composing a large `Write` sent **nothing** for minutes. |
| Model Gateway, not streamed | Sent nothing until the answer was whole. |
| Model Gateway, either | Ran on when its caller hung up: aiohttp's default leaves the handler running, and a stream learned its caller was gone only at its next write, which for a silent stream was the end. |
| Provider SDK | 360 s per attempt, two retries: a non-streamed answer longer than that failed after 18 minutes. |

In that test two Claude Code children (6575b312, 4c3052fb) each spent 18 to 24 of their 30 working minutes in one loop: the model spent 6 to 8 minutes composing a 22,000 to 28,000-token tool call; the egress cut the silent stream at 5 minutes; Claude Code asked again (once without streaming), and the model wrote the same call again. Every dropped call still ran to its end at the provider and was billed for output nobody read, and the last two non-streamed fallbacks ran 18 minutes each, past the run's end.

## Decision

**D1. Every streamed answer has a heartbeat.** When a stream has had nothing to send for `HEARTBEAT_S` (15 s), the gateway writes its wire's keepalive: Anthropic's own `event: ping` on Messages, an SSE comment (`: keepalive`) on Chat Completions and Responses. The next chunk keeps coming in its own task; only the silence is filled. Gemini's `@google/genai` reader (the one Gemini CLI 0.63 bundles) takes an event only from `data: ` at the start of its buffer, so a comment would stall it; its keepalive is an empty response, `data: {}`, which holds no candidate and no usage, so Gemini CLI's turn adds nothing for it (WP127-FU-1).

The heartbeat is the gateway's because only the gateway knows a call is alive. The alternatives fail on their own terms: raising the egress silence cut would keep dead upstreams too; streaming a tool call's argument deltas would change what the Messages wire treats as the truth about a call (its final response, in every provider family) and leaves hidden thinking silent anyway.

`HEARTBEAT_S` stays at most a quarter of `UPSTREAM_READ_TIMEOUT_S` (a test holds the two together), so a beat survives a slow write.

**D2. A call ends with its caller.** The gateway's server cancels the handler of a request whose client hung up (`handler_cancellation=True`). A stream's provider stream is closed with it; a non-streamed call's provider request is cancelled. Either is settled as a cut call: the estimate WP-66 already charges a stream the engine left, held to its bound. The heartbeat's writes are a second detector: a write to a gone client fails within a beat.

With D2 a non-streamed call can take at most the egress cut (300 s) end to end, and the provider SDK's retries of a read timeout never start. Engines stream; Claude Code falls back to a non-streamed request only after a stream failed, which D1 removes as a cause.

**D3. Nothing else changes its limit.** The engine's working time (1,800 s) and the run's deadline (3,600 s) stay. They were breached because the time went into a loop, not because the work needed it (WP-127 evidence: about 8 minutes of useful work in the second run). A person's approvals were already outside the working time (ADR-RUN-01 D4).

**Consequences.**

- An engine may wait as long as its model takes; only a model that sends nothing at all for 300 s, with the gateway also silent, is cut, and that means the gateway itself is stuck.
- A cut call is charged an estimate, not nothing: the provider may have billed what it made before the cancel.
- The wrap-up a cut run answers with is held to its output limit; one the limit cut now ends on its last whole paragraph (WP-127), under the run's notice that the result is partial.

## As built

WP-127 on `harness/v2-wp127`:

- D1: `GatewayWire.keepalive` (`harness_core/gateway/wire.py`) on each wire; `_sse` in `backend/python/harness_core/gateway/model_server.py` waits for the next chunk in its own task and writes the keepalive after `HEARTBEAT_S`; `ChatStream.keepalive` carries the wire's.
- D2: `ModelGatewayServer` builds its runner with `handler_cancellation=True`; `ModelGateway._completed` settles a cancelled non-streamed call as `interrupted` (estimated) and shields the settle of a finished one.
- The wrap-up's `whole_part` (`backend/python/harness_core/engines/durable/wrap_up.py`).
- Tests: `backend/python/tests/unit/harness_core/gateway/test_wp127_call_liveness.py`, `backend/python/tests/unit/harness_core/engines/durable/test_wp127_wrap_up_cut.py`.

FIX-EGD4 on `harness/v2-egd4`: D2 now holds before the provider's first event too. A stream whose handler is cancelled while it waits for that event (the provider has the request and is still thinking) was given back its slot and dropped: no `model_gateway.call`, no charge, its reservation open until it expired. `ModelGateway._open_stream` settles it as `interrupted`, like a stream cut later. Found by the durable lane's EG-D4 (the scripted model's held call when DE-5 kills the worker). Test: `backend/python/tests/unit/harness_core/gateway/test_egd4_cut_before_first_event.py`.

## Deviations

- Gemini's heartbeat is an empty response, not a comment (D1, WP127-FU-1). The gateway sends a keepalive only once a stream has begun, so a provider silent before its first event is not covered on any wire.

## Owner amendments

None.

## Status

**Accepted** 2026-11-08: decided from first principles, root cause, long term; built in WP-127.
