# Non-streaming chat endpoints

Design notes for the four JSON (non-SSE) chat routes and the Python routes behind them.
They cover what was broken and why, the high-level and low-level design, how data
moves through a turn, the error contract, and how the change is tested.

| Public route (Node, `/api/v1`) | Scope | Python route it calls |
| --- | --- | --- |
| `POST /conversations/create` (and `/conversations/internal/create`) | `conversation:write` | `POST /api/v1/chat`, or `/api/v1/agent/agentIdPlaceholder/chat` when `chatMode` is `agent` |
| `POST /conversations/:conversationId/messages` (and `/internal/...`) | `conversation:chat` | same as above |
| `POST /agents/:agentKey/conversations` | `agent:execute` | `POST /api/v1/agent/{agentKey}/chat` |
| `POST /agents/:agentKey/conversations/:conversationId/messages` | `agent:execute` | `POST /api/v1/agent/{agentKey}/chat` |

The frontend, the Slack bot and the in-repo Python services all use the `/stream`
twins. The non-streaming routes serve API and SDK callers: the published
`@pipeshub-ai/mcp` SDK ships `conversationsCreateConversation` and
`conversationsAddMessage`, and automation calls the routes directly.

## 1. What was broken

| # | Defect | Effect |
| --- | --- | --- |
| 1 | Python's `POST /api/v1/chat` was deleted with the LangGraph pipeline in the agent-loop rewrite (#2702, 2026-07-28). Node's `createConversation` and `addMessage` still called it. | **Every** assistant non-streaming turn failed: 404/405 from Python, conversation marked `Failed`. |
| 2 | The Node handlers ran the LLM call **inside** the MongoDB transaction that saved the user message. | On replica-set installs, any answer slower than MongoDB's 60 s transaction lifetime was aborted, and the retry logic of `withTransaction` could repeat the whole turn. |
| 3 | `AIServiceCommand.execute()` retries three times on transport errors by default. | A dropped connection re-ran the LLM, duplicating the answer and any tool side effects. |
| 4 | The four handlers were ~1,300 lines of hand-copied payload building that had drifted from the streaming handlers. | Missing `conversationId`, `runId`, `timezone` and `currentTime`; `chatMode: agent` was never routed to the universal agent; `tools` were ignored. |
| 5 | The agent non-streaming routes had no request validation. | Arbitrary bodies reached Mongo and Python. |
| 6 | `createConversation` resolved scoped-token callers inline. On a lookup failure it called `next(error)` and **kept going**. A missing query `throw`s outside `try`, and Express 4 does not catch rejected async handlers. | Double responses, and hung requests. |
| 7 | Python's agent `chat()` parsed SSE frames one chunk at a time, with no buffer. | A frame split across two chunks was silently lost, so the result was "The agent did not produce a response" even though the agent had answered. |
| 8 | The two assistant routes had been removed from the OpenAPI spec (#2282). `AgentConversation.status` documented values the code never writes (`INPROGRESS`/`COMPLETED`/`FAILED`), and `Conversation.status` lacked `Stopped`. | The SDK and API docs were wrong. |
| 9 | Frontend `chatApi.fetchMessages` and `chatApi.createConversation` targeted `/api/chat/conversations`, a route that does not exist. | Dead code that would fail if anyone called it. |

## 2. High-level design

The principle: **one pipeline, two framings.** The non-streaming route runs exactly
the code its `/stream` twin runs and only changes how the result leaves each process.

```mermaid
flowchart LR
    subgraph Client
      C[API / SDK / automation]
    end
    subgraph Node["Node.js API (Express, :3000)"]
      R[es.routes.ts<br/>auth → scopes → zod validation]
      H[non-streaming-chat.controller.ts<br/>one handler, 4 routes]
      T[non-streaming-chat.ts<br/>3-phase turn]
      P[ai-chat-payload.ts<br/>shared with /stream handlers]
      U[utils.ts<br/>saveCompleteConversation / markConversationFailed]
    end
    subgraph Py["Query service (FastAPI, :8000)"]
      A1["POST /api/v1/chat<br/>askAI"]
      A2["POST /api/v1/agent/{id}/chat<br/>chat"]
      S1["/chat/stream pipeline<br/>_generate_chat_stream_via_agent_loop"]
      S2["/agent/{id}/chat/stream pipeline<br/>chat_stream"]
      K[stream_collector.py<br/>SSE → one JSON outcome]
    end
    M[(MongoDB<br/>chatSessions / messages / citations)]
    L[LLM + retrieval + tools]

    C -->|JSON| R --> H --> T
    T --> P
    T -->|phase 1 & 3| U --> M
    T -->|phase 2, JSON| A1 & A2
    A1 --> S1 --> K
    A2 --> S2 --> K
    S1 & S2 --> L
    K -->|completion_data or error| T
```

Why this shape:

- **No second pipeline in Python.** `askAI` and `chat` drive the same generator the
  streaming routes return and hand it to `collect_stream_outcome`. Security-sensitive
  setup (credential scoping, toolset loading, LLM resolution) exists once.
- **No second persistence path in Node.** Phase 3 is the same
  `saveCompleteConversation` / `markConversationFailed` the streaming routes call,
  so a conversation created without streaming is indistinguishable from a streamed one.
- **No second payload builder.** `buildAiChatRequest` builds the AI request body for
  both the streaming and non-streaming handlers, so the two cannot drift again (the
  cause of defect 4).

## 3. Low-level design

### 3.1 Node modules

```mermaid
classDiagram
    direction LR
    class non_streaming_chat_controller {
      +createConversation(appConfig) TurnHandler
      +addMessage(appConfig) TurnHandler
      +createAgentConversation(appConfig) TurnHandler
      +addMessageToAgentConversation(appConfig) TurnHandler
      -nonStreamingTurn(appConfig, mode)
      -targetOf(req) ChatTarget
      -sessionFields(target, body, userId, orgId)
    }
    class non_streaming_chat {
      +CONVERSATION_ID_HEADER
      +openConversation(fields, userMessage) Conversation
      +continueConversation(filter, userMessage) ContinuedConversation
      +completeTurn(options) CompletedTurn
      -inShortTransaction(work)
      -clientErrorFor(error, failReason)
      -rejectionFor(status, failReason)
    }
    class ai_chat_payload {
      +ChatTarget
      +buildAiChatRequest(target, body, context) AiChatRequest
      +parseChatMode(mode)
      +assignToolsToPayload()
      +assignCallerContextToAiPayload()
      +assignAgentCapabilitiesToPayload()
    }
    class scoped_request {
      +hydrateScopedRequestAsUser(req, appConfig)
      +checkServiceAccountAccess(req, appConfig)
      +stableObjectIdHexForExternalEmail(email)
    }
    class utils {
      +saveCompleteConversation()
      +markConversationFailed()
      +markAgentConversationFailed()
      +appendMessages()
      +getMessages()
      +formatPreviousConversations()
    }
    class es_controller {
      +streaming handlers
      +re-exports for existing importers
    }
    non_streaming_chat_controller --> scoped_request
    non_streaming_chat_controller --> non_streaming_chat
    non_streaming_chat_controller --> ai_chat_payload
    non_streaming_chat --> utils
    non_streaming_chat --> AIServiceCommand : maxAttempts 1
    es_controller --> ai_chat_payload
    es_controller --> scoped_request
```

- **Single responsibility.** The controller maps HTTP to a turn. `non-streaming-chat.ts`
  owns the transaction boundary and the AI call. `ai-chat-payload.ts` owns the wire
  contract with Python. `scoped-request.ts` owns internal-caller identity.
- **Open/closed.** A new target kind means a new `ChatTarget` variant and one branch in
  `buildAiChatRequest`. A new route that starts a chat reuses `nonStreamingTurn`.
- **No cycles.** `scoped-request.ts` was extracted from `es_controller.ts` so the new
  controller does not import the 8,000-line controller. `es_controller` re-exports
  everything it used to export, so no importer changes.

### 3.2 Python modules

```mermaid
classDiagram
    direction LR
    class chatbot_py {
      +askAI(request) JSONResponse
      +askAIStream(request) StreamingResponse
      -_parse_chat_query(request, registry, streaming)
      -_generate_chat_stream_via_agent_loop(...)
    }
    class agent_py {
      +chat(request, agent_id) JSONResponse
      +chat_stream(request, agent_id) StreamingResponse
    }
    class stream_collector_py {
      +SSEFrameParser_feed(chunk)
      +SSEFrameParser_flush()
      +collect_stream_outcome(body_iterator, is_disconnected) StreamOutcome
      +StreamOutcome_to_response() JSONResponse
    }
    chatbot_py --> stream_collector_py
    agent_py --> stream_collector_py
    agent_py ..> agent_py : chat drives chat_stream
```

`collect_stream_outcome` rules:

1. Frames are buffered until a blank line, so a frame split across chunks is parsed
   once it is whole. CRLF line endings, multi-line `data:` fields, `: keep-alive`
   comments and a missing final blank line are all handled.
2. Frames carrying `parentRunId` belong to sub-agents and are ignored. A sub-agent's
   `RUN_ERROR` is handed back to its parent as a tool result, and the parent still answers.
3. The completion is the root `RUN_FINISHED.result`, or a legacy `complete` frame.
   The **last** root terminal frame wins: a `RUN_ERROR` after `RUN_FINISHED` means the
   answer was not saved, and a graceful `RUN_FINISHED` after a `RUN_ERROR` (see
   `agui_emitter.py`) is the run's real result.
4. `is_disconnected` is checked between chunks. AG-UI heartbeats arrive at least every
   15 s, so a caller that gave up stops the agent loop within that window. The iterator
   is always `aclose()`d, and both route adapters wrap their bridge in
   `contextlib.aclosing`, so closing the outer stream cancels the bridge's producer and
   heartbeat tasks at once. (`async for` does not close the generator it iterates, so
   without it they ran until garbage collection. This applies to `/stream` disconnects too.)

`_parse_chat_query` is the request prelude shared by `/chat` and `/chat/stream`: JSON
parse, `ChatQuery` validation, the 409 for a `runId` that is already active, and the
`chat_session_started` telemetry event, which now carries `streaming: true|false`.
`agent.py::chat` sets `request.state.chat_streaming = False`, so the `agent_run` event
reports the real mode.

## 4. Data flow

### 4.1 Successful turn

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant N as Node handler
    participant DB as MongoDB
    participant P as Python /chat
    participant G as agent loop (same as /stream)

    C->>N: POST /conversations/create {query, chatMode, ...}
    N->>N: zod validation, XSS / format-specifier check
    N->>N: hydrateScopedRequestAsUser (no-op for user tokens)
    rect rgb(235, 245, 255)
    note over N,DB: Phase 1: short transaction (replica set only)
    N->>DB: insert chatSession (status Inprogress)
    N->>DB: append user_query message
    N->>DB: commit, endSession
    end
    N->>N: buildAiChatRequest(target, body, {conversationId, history, project})
    rect rgb(245, 245, 245)
    note over N,G: Phase 2: no transaction, maxAttempts 1, 620 s budget
    N->>P: POST /api/v1/chat (JSON, user JWT)
    P->>G: _generate_chat_stream_via_agent_loop
    G-->>P: AG-UI frames … RUN_FINISHED{result}
    P->>P: collect_stream_outcome → completion_data
    P-->>N: 200 completion_data
    end
    rect rgb(235, 255, 240)
    note over N,DB: Phase 3: same helper as /stream
    N->>DB: save citations, append bot_response, status Complete
    end
    N-->>C: 201 {conversation, meta} + X-Conversation-Id
```

A follow-up differs only in phase 1: `continueConversation` loads the conversation,
scoped to `{_id, orgId, userId, isDeleted: false}` plus assistant-or-agent (and
`agentKey`). It reads the history *before* appending the new question, then sets
`Inprogress`, all in one transaction. Project scope comes from the stored
conversation, never from the request.

### 4.2 Failure turn

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant N as Node handler
    participant DB as MongoDB
    participant P as Python

    C->>N: POST /agents/k/conversations {query}
    N->>DB: Phase 1 commits (conversation + question)
    N->>P: POST /api/v1/agent/k/chat
    alt Python returns 4xx (e.g. 424 llm_not_configured)
        P-->>N: 424 {code, message}
        N->>DB: error message + status Failed + conversationErrors[code]
        N-->>C: 424 {error.message = Python's user-facing message} + X-Conversation-Id
    else Python returns 5xx
        P-->>N: 500 {...}
        N->>DB: Failed with the generic "Something went wrong" reason
        N-->>C: 500 generic message (upstream text never shown)
    else unreachable (ECONNREFUSED) or timed out
        N->>DB: Failed ("couldn't answer right now" / "interrupted")
        N-->>C: 500 "trouble reaching one of its services" (no retry)
    else 200 without an answer
        N->>DB: Failed, errorType no_response
        N-->>C: 500
    end
```

### 4.3 Request field mapping (Node → Python)

`buildAiChatRequest` produces the payload for both framings; the streaming handlers
add `protocol: "agui"` on top.

| Field | Assistant | Agent | Source |
| --- | --- | --- | --- |
| `query`, `filters`, `attachments` | ✓ | ✓ | body (defaults `{}` / `[]`) |
| `previousConversations` | ✓ | ✓ | body on create; stored history on follow-ups |
| `recordIds` | create only | create only | body |
| `conversationId` | ✓ | ✓ | the conversation phase 1 wrote |
| `modelKey`, `modelName`, `modelFriendlyName`, `reasoningEffort`, `timezone`, `currentTime`, `runId` | ✓ | ✓ | body, `null` when absent |
| `chatMode` | `parseChatMode` (`agent:<m>` → `<m>`) | body or `quick` (never `auto`, which Python treats as "run the tier classifier") | body |
| `tools`, `agentCapabilities` | only when `chatMode` is agent | ✓ | body |
| `quickMode` | — | create only | body |
| `callerDisplayName`, `callerEmail` | — | ✓ | body (internal callers) |
| `projectInstructions`, `strictScope`, narrowed `filters`/`tools` | ✓ | ✓ | `applyProjectScope` (after `tools`) |

### 4.4 Conversation status

```mermaid
stateDiagram-v2
    [*] --> Inprogress: phase 1 commit
    Inprogress --> Complete: answer saved
    Inprogress --> Failed: 4xx / 5xx / unreachable / no answer / save error
    Inprogress --> Failed: classified failure answer (answerMatchType Error)
    Inprogress --> Stopped: run cancelled via the cancel route
    Complete --> Inprogress: next follow-up
    Failed --> Inprogress: next follow-up (failReason cleared)
    Stopped --> Inprogress: next follow-up
```

A crash between phases leaves `Inprogress` with the question saved: the same state a
crashed streaming turn leaves, and the next follow-up moves it on.

## 5. Error contract

| Python outcome | Python HTTP | Node → client | Persisted `errorType` |
| --- | --- | --- | --- |
| `RUN_FINISHED` | 200 | 201 / 200 with the conversation | — |
| `RUN_ERROR` `invalid_request` / `request_failed` | 400 | 400, message kept | same code |
| `request_too_large` | 413 | 413, message kept | same |
| `content_filter` | 422 | 422, message kept | same |
| `llm_not_configured` / `llm_initialization_failed` | 424 | 424, message kept | same |
| `toolset_config_missing` / `mcp_server_config_missing` | 424 | 424, message kept ("connect your actions in Workspace → Actions") | same |
| `rate_limit` / `quota_exceeded` | 429 | 429, message kept | same |
| `auth_error` / `server_error` (provider) | 502 | 500, generic message | same |
| `timeout` | 504 | 500, "unavailable" | same |
| unknown code / no terminal frame | 500 | 500, generic message | code / `no_response` |
| agent not found / no access (raised before the stream) | 404 / 403 | 404 / 403, message kept | `ai_service_error` |
| Node cannot reach Python | — | 500 `SERVICE_UNAVAILABLE_MESSAGE` | `internal_error` |

Node only surfaces the text of a sub-500 reply (`userFacingStatusError`). That is why
Python maps codes whose message tells the user what to do (configure a model, slow
down, shorten the input) below 500, and keeps provider and infrastructure failures at
5xx, so their text never reaches the user.

Every response after phase 1, success or failure, carries `X-Conversation-Id`. A
caller whose *first* turn failed can still `GET` or continue that conversation.

## 6. API contract

Documented in `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`
(`x-pipeshub-sdk: true`, so the generated SDK keeps them):

| operationId | Request schema | Success |
| --- | --- | --- |
| `createConversation` | `CreateConversationRequest` | `201 CreateConversationResponse` |
| `addMessage` | `AddMessageRequest` | `200 AddMessageResponse` |
| `createAgentConversation` | `AgentCreateConversationRequest` | `201 CreateAgentConversationResponse` |
| `addAgentConversationMessage` | `AgentAddMessageRequest` | `200 AddMessageResponse` |

`AgentStreamCreateConversationRequest` and `AgentAddMessageStreamRequest` are now
`allOf: [<base>, {required: [chatMode]}]` over the new base schemas, so the streaming
and non-streaming contracts cannot drift. The new header `components.headers.X-Conversation-Id`
is referenced from every response after phase 1.

## 7. Operational notes

- **Latency.** The response arrives only when the whole answer is ready. Agent runs can
  take minutes. Node's AI call budget is 620 s (`AIServiceCommand`). Proxies in front
  of Node (ingress, ALB, nginx) need an idle timeout above the expected answer time.
  Interactive clients should use `/stream`.
- **No retries.** `completeTurn` passes `maxAttempts: 1`. An LLM run is not idempotent.
- **Client gives up.** If the client disconnects from Node, the turn still finishes and
  is persisted. The answer shows up in `GET /conversations/:id`. If Node gives up on
  Python (the 620 s budget), Python notices at its next chunk and stops the agent loop.
- **Cancellation.** Send a `runId` and call `POST …/cancel {runId}` from another
  request. The turn ends `Stopped` and returns 200.
- **Telemetry.** `chat_session_started.streaming` and `agent_run.streaming` distinguish
  the two framings.

## 8. Tests

| Layer | File | What it pins |
| --- | --- | --- |
| Python unit | `tests/unit/agents/agent_loop/protocol/test_stream_collector.py` | frame buffering across chunks, CRLF, multi-line data, comments, flush; root vs sub-agent frames; completion-wins; disconnect stops and closes the stream; error-code → status table |
| Python unit | `tests/unit/api/routes/test_chatbot_non_streaming.py` | `askAI` drives the same generator as `/chat/stream` with the same `ChatQuery`; 400/409 prelude; 424 mapping; telemetry `streaming: false` |
| Python unit | `tests/unit/api/routes/test_agent_chat_non_streaming.py` | agent `chat()` over AG-UI frames, split frames, status mapping |
| Python in-process e2e | `tests/integration/test_chat_stream_agent_loop_e2e.py::TestChatNonStreamingAgentLoopEndToEnd` | real route → bridge → agent factory → finalizer chain, only outer I/O mocked, all chat modes, missing LLM |
| Node flow | `tests/.../controller/es_controller.non-streaming.test.ts` | all four handlers against the in-memory Mongo store and a fake AI backend: persistence, response shape, `X-Conversation-Id`, AI URL/payload/history, citations, 4xx/5xx/unreachable/no-answer/classified/stopped, a single fetch (no retry), XSS rejection with zero writes, ownership and cross-type 404s, scoped-token re-signing, replica-set session ended before the AI call |
| Node unit | `tests/.../utils/ai-chat-payload.test.ts`, `tests/.../validators/es_validators.non-streaming.test.ts`, `es.routes.test.ts` | payload builder, new schemas, validation wired on the agent routes |
| Live e2e | `integration-tests/response-validation/enterprise-search/conversations/integration_test_conversation_non_streaming.py` | against a running stack: bodies validated against the OpenAPI operation, persisted state re-read via `GET`, KB-grounded answers, follow-ups, validation 400s, auth, 404 scoping, unknown agent leaves a `Failed` conversation named by `X-Conversation-Id` |

Run them:

```bash
# Python
cd backend/python && pytest tests/unit/agents/agent_loop/protocol \
  tests/unit/api/routes/test_chatbot_non_streaming.py \
  tests/unit/api/routes/test_agent_chat_non_streaming.py \
  tests/integration/test_chat_stream_agent_loop_e2e.py

# Node
cd backend/nodejs/apps && npm test -- --grep "non-streaming|ai-chat-payload"

# Live stack (see integration-tests/README.md for env setup)
cd integration-tests && pytest -m integration \
  response-validation/enterprise-search/conversations/integration_test_conversation_non_streaming.py
```

## 9. Follow-ups

- `regenerateAnswers` / `regenerateAgentAnswers` still build their AI payloads by hand;
  moving them onto `buildAiChatRequest` would finish the DRY work.
- `hydrateScopedRequestAsUser` resolves Slack service accounts only when `:agentKey` is
  present. There is no internal (scoped-token) non-streaming agent route today. Add one
  only if a caller needs it.
