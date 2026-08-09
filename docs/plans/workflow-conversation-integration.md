# Workflow–Conversation Integration: Implementation Plan

## 1. Problem Statement

Five gaps in the current workflow execution system:

| # | Gap | Root Cause |
|---|-----|------------|
| 1 | Workflow execution results are not visible in the originating chat | `_append_run_result_to_conversation` appends a raw `bot_response` message but the chat UI has no renderer for it; no `conversation_id` is passed to the workflow run context for streamed output during execution |
| 2 | No way to see which workflows are connected to a conversation | The `Conversation` Mongoose schema has no `workflowIds` field; the frontend has no sidebar/panel for connected workflows |
| 3 | Workflow metadata (trigger type, execution kind, next run, event subscriptions) is hard to inspect | The `WorkflowCard` only shows trigger kind/next-run; the detail view requires navigating to `/workflows?workflowId=xxx` |
| 4 | No pre-creation prerequisite verification exposed to the user | `PrerequisiteValidator` runs at `TaskEngine.create()` and `TaskExecutor._check_prerequisites()`, but the agent tool doesn't surface a preview; `fire_at` in the past is silently accepted |
| 5 | No in-chat dry-run capability | `TaskEngine.dry_run()` exists, `WorkflowService.dry_run()` exists, REST `POST /:workflowId/dry-run` exists, but the `workflow_manage` tool has no `dry_run` action and the chat UI has no trigger for it |

---

## 2. Current Architecture (As-Is)

### 2.1 Data Flow — Workflow Creation

```
┌─────────────┐     SSE/agui      ┌──────────────────┐     Kafka        ┌───────────────┐
│  Frontend    │ ◄──────────────── │  Python Query    │ ──────────────► │  Python Query  │
│  Chat UI     │                   │  (Agent Loop)    │  task_run_      │  (TaskExecutor)│
│              │ ── user message → │                  │  dispatch       │                │
└─────────────┘                   │  workflow_manage  │                 │  assemble →    │
                                  │  tool call        │                 │  run agent     │
                                  │                   │                 │                │
                                  │  → TaskEngine     │                 │  → result      │
                                  │    .create()      │                 │    ↓           │
                                  │    ↓              │                 │  _finalize_    │
                                  │  ArangoDB (task)  │                 │  result()      │
                                  │  Redis (triggers) │                 │    ↓           │
                                  │  Redis (runs)     │                 │  _notify()     │
                                  └──────────────────┘                 │    ↓           │
                                                                       │  Kafka →       │
                                                                       │  notification  │
                                                                       └───────────────┘
                                                                              │
                                                                              ▼
                                                              ┌───────────────────────────┐
                                                              │  Node.js                   │
                                                              │  NotificationConsumer      │
                                                              │  → emitWorkflowRunUpdate() │
                                                              │  → Socket.IO to frontend   │
                                                              └───────────────────────────┘
```

### 2.2 Key Files (Existing)

**Python Backend**
| File | Responsibility |
|------|----------------|
| `services/tasks/domain/models.py` | `TaskDefinition`, `TaskRun`, `TaskTrigger`, `TaskStatus`, `RunStatus` |
| `services/tasks/application/engine.py` | `TaskEngine` — CRUD, lifecycle, dispatch, fire_event |
| `services/tasks/application/prerequisites.py` | `PrerequisiteValidator` — connector/collection/toolset checks |
| `services/tasks/runtime/executor.py` | `TaskExecutor` — consumes `TASK_EVENTS`, drives runs to completion |
| `services/tasks/runtime/spec_assembler.py` | Assembles `Agent` + `Goal` from a `TaskDefinition` |
| `services/workflows/domain/models.py` | `Workflow`, `WorkflowVersion`, `RunResultMessage`, `WorkflowIR` |
| `services/workflows/application/workflow_service.py` | `WorkflowService` — thin facade over `TaskEngine` |
| `services/workflows/adapters/node/conversation_writer.py` | `NodeConversationWriter` — POSTs results to Node internal route |
| `services/workflows/codegen/agent.py` | `WorkflowBuilderAgent` — LLM-driven code generation |
| `agents/agent_loop/tools/tasks/workflow_manage.py` | `WorkflowManageTool` — agent tool for create/update/pause/resume/cancel/run_now |
| `agents/agent_loop/tools/tasks/workflow_find.py` | `WorkflowFindTool` — agent tool for list/search/get |
| `agents/agent_loop/tasks_wiring.py` | Wires task/workflow tools into agent factory |
| `agents/agent_loop/hooks/task_scheduled_card.py` | SSE hook: emits `workflow_created` card on successful create |
| `agents/agent_loop/hooks/task_side_effect.py` | Tracks write side effects for safe retry decisions |
| `services/tasks/interface/notifier.py` | `ITaskNotifier`, `TaskNotification`, `TaskNotificationKind` |

**Node.js Backend**
| File | Responsibility |
|------|----------------|
| `modules/workflows/controller/workflows.controller.ts` | Proxy controller forwarding to Python `/api/v1/workflows` |
| `modules/workflows/routes/workflows.routes.ts` | Public auth-gated routes |
| `modules/workflows/routes/workflows-internal.routes.ts` | Internal route for `NodeConversationWriter.append_result()` |
| `modules/notification/service/notification.consumer.ts` | Kafka consumer → Socket.IO `workflowRunUpdate` event |
| `modules/notification/service/notification.service.ts` | Socket.IO server, `emitWorkflowRunUpdate()` |
| `modules/enterprise_search/schema/conversation.schema.ts` | Mongoose `Conversation` schema (no workflow fields today) |

**Frontend**
| File | Responsibility |
|------|----------------|
| `app/(main)/workflows/page.tsx` | Workflow dashboard list + detail view |
| `app/(main)/workflows/types.ts` | `Workflow`, `WorkflowRun`, `WorkflowTrigger` types |
| `app/(main)/workflows/api.ts` | `WorkflowsApi` — REST calls to Node proxy |
| `app/(main)/workflows/components/workflow-detail-view.tsx` | Single workflow detail |
| `app/(main)/chat/components/message-area/workflow-card.tsx` | In-chat `WorkflowCard` component |
| `app/(main)/chat/types.ts` | `WorkflowCardPayload`, `ScheduledTaskPayload` |
| `app/(main)/chat/streaming.ts` | SSE handler — `applyScheduledTaskSse()` |

### 2.3 Conversation Schema (No Workflow Fields)

The `conversationSchema` (Mongoose) currently has no field tracking connected workflows. The `TaskDefinition` has `created_from_conversation_id: str | None` which is set at creation time — this is the only link, and it's unidirectional (task → conversation, not conversation → task).

---

## 3. High-Level Design (HLD)

### 3.1 Design Goals

1. **Bidirectional conversation–workflow link**: Conversation knows its workflows; workflow knows its conversation.
2. **Run results appear in-chat**: Both final results and intermediate progress are visible in the originating conversation.
3. **Workflow metadata panel**: A sidebar/drawer in chat showing connected workflows with rich metadata.
4. **Pre-creation validation exposed**: The agent surfaces prerequisite results AND schedule feasibility before creating.
5. **In-chat dry-run**: A single tool action triggers a dry run and streams output to the conversation.
6. **Clicking a workflow anywhere navigates to its originating chat** (with fallback to dashboard).

### 3.2 Architecture (To-Be)

```
┌─────────────────────────────────────────────────────────────────┐
│                       Frontend (Next.js)                        │
│                                                                 │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────┐ │
│  │ ChatPage          │  │ WorkflowPanel    │  │ WorkflowPage │ │
│  │                   │  │ (new sidebar)    │  │ (existing)   │ │
│  │ WorkflowCard v2   │  │                  │  │              │ │
│  │ RunResultCard     │  │ - list connected │  │ "Open Chat"  │ │
│  │ DryRunCard        │  │   workflows      │  │ button →     │ │
│  │ PrereqCheckCard   │  │ - metadata       │  │ /chat?cid=X  │ │
│  │                   │  │ - dry-run button  │  │              │ │
│  └──────────────────┘  └──────────────────┘  └──────────────┘ │
│           │                     │                     │         │
│           ▼ SSE                 ▼ REST                ▼ REST   │
│     ┌───────────┐         ┌───────────┐         ┌──────────┐  │
│     │ Socket.IO │         │ /api/v1/  │         │ /api/v1/ │  │
│     │ events    │         │ conv/     │         │ workflows│  │
│     └─────┬─────┘         │ workflows │         └──────────┘  │
└───────────┼───────────────┴─────┬─────┴────────────────────────┘
            │                     │
            ▼                     ▼
┌───────────────────────────────────────────────────────────────┐
│                     Node.js API (Express)                     │
│                                                               │
│  NotificationConsumer ──► emitWorkflowRunUpdate (existing)    │
│  NEW: emitConversationWorkflowUpdate (connected wf changes)  │
│                                                               │
│  Conversation Schema + new `connectedWorkflowIds: [String]`  │
│  NEW internal route: GET /conversations/:id/workflows        │
│  PATCH internal: add/remove workflow from conversation       │
│  EXISTING internal: POST /conversations/:id/messages         │
└───────────────────────────────────────────────────────────────┘
            │                     │
            ▼                     ▼
┌───────────────────────────────────────────────────────────────┐
│                     Python (Query Service)                     │
│                                                               │
│  workflow_manage tool:                                         │
│    + dry_run action                                            │
│    + validate_prerequisites action (explicit preview)          │
│    + _create → also PATCHes conversation to add workflowId    │
│                                                               │
│  TaskExecutor._append_run_result_to_conversation:             │
│    Enhanced to include structured RunResultCard payload        │
│    (not just a raw text message)                               │
│                                                               │
│  NodeConversationWriter:                                       │
│    + link_workflow(conversation_id, workflow_id)               │
│    + unlink_workflow(conversation_id, workflow_id)             │
│    + write_structured(conversation_id, card_payload)           │
└───────────────────────────────────────────────────────────────┘
```

### 3.3 Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Where to store conversation↔workflow link | MongoDB `Conversation.connectedWorkflowIds: [String]` (Node side) + existing `TaskDefinition.created_from_conversation_id` (Python side) | Bidirectional: conversation can list its workflows, workflow can find its conversation. Conversation is MongoDB (Node owns), task is ArangoDB (Python owns). No new cross-DB transaction needed — link is best-effort. |
| How run results appear in chat | Structured `tool_call` message with `toolName: "workflow_run_result"` instead of plain `bot_response` | The frontend can render a rich `RunResultCard` component; plain text doesn't support status badges, action buttons, or metadata |
| Clicking workflow → navigates to | `/chat?conversationId=<cid>` when `created_from_conversation_id` exists, fallback to `/workflows?workflowId=<id>` | Chat is the natural context for the workflow; dashboard is the fallback for headless/API-created workflows |
| Dry run integration | New `dry_run` action on `workflow_manage` tool + SSE `workflow_dry_run_started` card + Socket.IO `workflowRunUpdate` events | Reuses existing `TaskEngine.dry_run()` machinery; only the tool action and frontend card are new |
| Prerequisite preview | New `validate` action on `workflow_manage` tool (read-only, no side effects) | Non-blocking preview before `create`; the agent can show results to user and ask for confirmation |
| Workflow metadata in chat | Fetched on-demand by frontend via REST when workflow panel is opened, not embedded in SSE stream | Keeps SSE lean; metadata changes (trigger updates, run completions) arrive via Socket.IO independently |

---

## 4. Low-Level Design (LLD)

### 4.1 Data Model Changes

#### 4.1.1 MongoDB: Conversation Schema (Node.js)

```typescript
// conversation.schema.ts — ADD to conversationSchema
connectedWorkflowIds: [{ type: String }],  // workflow_id strings (UUID from ArangoDB)
```

**Index**: None needed — `connectedWorkflowIds` is only read when loading a single conversation by `_id`.

#### 4.1.2 TaskDefinition (Python) — No Change

`created_from_conversation_id` already exists. No new fields needed.

#### 4.1.3 RunResultMessage Enhancement (Python)

```python
# services/workflows/domain/models.py — enhance RunResultMessage
class RunResultMessage(BaseModel):
    workflow_id: str
    workflow_name: str           # NEW — for display without a follow-up fetch
    run_id: str
    status: str
    output_summary: str | None = None
    error: str | None = None     # NEW — for failed runs
    is_dry_run: bool = False     # NEW
    trigger_kind: str | None = None  # NEW — "cron" / "event" / "one_time" / etc.
    redirect_link: str
    started_at: str | None = None    # NEW
    completed_at: str | None = None  # NEW
```

### 4.2 Interface Definitions

#### 4.2.1 IConversationWriter (Python Port — Enhanced)

```python
# services/workflows/interface/conversation_writer.py (NEW)
class IConversationWriter(ABC):
    @abstractmethod
    async def append_result(
        self, conversation_id: str, org_id: str, msg: RunResultMessage
    ) -> None: ...

    @abstractmethod
    async def link_workflow(
        self, conversation_id: str, org_id: str, workflow_id: str
    ) -> None:
        """Add workflow_id to the conversation's connectedWorkflowIds array."""

    @abstractmethod
    async def unlink_workflow(
        self, conversation_id: str, org_id: str, workflow_id: str
    ) -> None:
        """Remove workflow_id from the conversation's connectedWorkflowIds array."""
```

#### 4.2.2 Node.js Internal Routes (Enhanced)

```
PATCH /api/v1/workflows/internal/conversations/:conversationId/workflows
  Body: { action: "add" | "remove", workflowId: string }
  → Adds/removes from connectedWorkflowIds array

GET /api/v1/workflows/internal/conversations/:conversationId/workflows
  → Returns { workflowIds: string[] }

POST /api/v1/workflows/internal/conversations/:conversationId/messages  (existing, enhanced)
  Body: { ...RunResultMessage fields... }
  → Appends structured tool_call message instead of plain bot_response
```

#### 4.2.3 Frontend API Extension

```typescript
// frontend/app/(main)/chat/api.ts — ADD
export const ConversationWorkflowsApi = {
  async getConnectedWorkflows(conversationId: string): Promise<Workflow[]>;
};
```

### 4.3 New Components

#### 4.3.1 Frontend Components

| Component | File | Description |
|-----------|------|-------------|
| `WorkflowPanel` | `chat/components/workflow-panel.tsx` | Slide-out panel in chat showing connected workflows with metadata, status, triggers, actions (dry-run, pause, view runs) |
| `RunResultCard` | `chat/components/message-area/run-result-card.tsx` | In-chat card rendered when a workflow run completes, showing status/output/error with link to detail |
| `DryRunCard` | `chat/components/message-area/dry-run-card.tsx` | In-chat card showing dry-run progress and results (reuses RunResultCard with visual differentiation) |
| `PrereqCheckCard` | `chat/components/message-area/prereq-check-card.tsx` | In-chat card showing prerequisite validation results (pass/fail per connector/collection/toolset) |
| `WorkflowMetadataBadges` | `chat/components/message-area/workflow-metadata-badges.tsx` | Compact badge row showing trigger type, execution kind, next run time |

#### 4.3.2 Python Tool Additions

| Tool/Action | File | Description |
|-------------|------|-------------|
| `workflow_manage(action="dry_run")` | `tools/tasks/workflow_manage.py` | Executes a dry run, returns run_id for tracking |
| `workflow_manage(action="validate")` | `tools/tasks/workflow_manage.py` | Runs `PrerequisiteValidator.validate()` without creating anything |

#### 4.3.3 SSE Events (New)

| Event Name | Trigger | Payload |
|------------|---------|---------|
| `workflow_dry_run_started` | `workflow_manage(action="dry_run")` succeeds | `{ workflowId, runId, title }` |
| `prerequisite_check_result` | `workflow_manage(action="validate")` completes | `{ workflowId?, issues: [{kind, id, reason, blocking}], ok }` |

### 4.4 Enhanced WorkflowCard v2

The existing `WorkflowCard` gets enriched:

```
┌─────────────────────────────────────────────────────┐
│ 🔀 Workflow Scheduled                    ● active   │
│ "Daily Jira Digest"                                 │
│                                                     │
│ ┌─────────┐ ┌──────────────┐ ┌──────────────────┐ │
│ │🔄 cron  │ │ agent_task   │ │ Next: Aug 9 9AM  │ │
│ └─────────┘ └──────────────┘ └──────────────────┘ │
│                                                     │
│ [Dry Run]  [View Details]  [Open in Dashboard →]   │
│                                                     │
│ ⚠ Prerequisites: Jira connector auth expires in 2d │
└─────────────────────────────────────────────────────┘
```

### 4.5 Conversation Workflow Panel

Accessible via a toolbar button in the chat header:

```
┌────────────────────────────────────────┐
│ Connected Workflows (2)           [×]  │
│────────────────────────────────────────│
│                                        │
│ ┌────────────────────────────────────┐ │
│ │ 📋 Daily Jira Digest              │ │
│ │ Status: active  │  Type: cron     │ │
│ │ Next run: Aug 9, 9:00 AM          │ │
│ │ Last run: succeeded (Aug 8, 9:01) │ │
│ │                                    │ │
│ │ [Dry Run] [Pause] [View Runs]     │ │
│ └────────────────────────────────────┘ │
│                                        │
│ ┌────────────────────────────────────┐ │
│ │ 📋 Slack Alert on PR Merge        │ │
│ │ Status: active  │  Type: event    │ │
│ │ Event: github.pull_request.merged │ │
│ │ Last run: succeeded (Aug 7)       │ │
│ │                                    │ │
│ │ [Dry Run] [Pause] [View Runs]     │ │
│ └────────────────────────────────────┘ │
│                                        │
└────────────────────────────────────────┘
```

---

## 5. Directory Structure — Changed & New Files

### 5.1 Python Backend

```
backend/python/app/
├── services/
│   ├── workflows/
│   │   ├── domain/
│   │   │   └── models.py                          # MODIFIED — enhanced RunResultMessage
│   │   ├── interface/
│   │   │   └── conversation_writer.py             # NEW — IConversationWriter ABC
│   │   ├── adapters/
│   │   │   └── node/
│   │   │       └── conversation_writer.py         # MODIFIED — add link/unlink_workflow methods
│   │   └── application/
│   │       └── workflow_service.py                # MODIFIED — add dry_run with conversation_id
│   ├── tasks/
│   │   ├── application/
│   │   │   └── engine.py                          # MODIFIED — dry_run returns enriched data
│   │   └── runtime/
│   │       └── executor.py                        # MODIFIED — enhanced _append_run_result_to_conversation
│   └── agents/
│       └── agent_loop/
│           ├── tools/
│           │   └── tasks/
│           │       ├── workflow_manage.py          # MODIFIED — add dry_run, validate actions
│           │       └── workflow_find.py            # MODIFIED — add conversation_id to overview
│           ├── hooks/
│           │   ├── task_scheduled_card.py          # MODIFIED — emit enhanced card with metadata badges
│           │   ├── workflow_dry_run_card.py        # NEW — SSE card for dry-run started
│           │   └── prereq_check_card.py            # NEW — SSE card for prerequisite check results
│           └── tasks_wiring.py                    # MODIFIED — register new hooks
```

### 5.2 Node.js Backend

```
backend/nodejs/apps/src/
├── modules/
│   ├── workflows/
│   │   ├── routes/
│   │   │   └── workflows-internal.routes.ts       # MODIFIED — add PATCH/GET workflow link routes
│   │   └── controller/
│   │       └── workflows.controller.ts            # MODIFIED — add conversation link endpoint (if needed)
│   ├── enterprise_search/
│   │   ├── schema/
│   │   │   └── conversation.schema.ts             # MODIFIED — add connectedWorkflowIds field
│   │   └── types/
│   │       └── conversation.interfaces.ts         # MODIFIED — add connectedWorkflowIds to IConversation
│   └── notification/
│       └── service/
│           └── notification.service.ts            # MODIFIED — add emitConversationWorkflowsChanged
```

### 5.3 Frontend

```
frontend/app/(main)/
├── chat/
│   ├── types.ts                                    # MODIFIED — enhanced WorkflowCardPayload, new RunResultCardPayload
│   ├── api.ts                                      # MODIFIED — add getConversationWorkflows, triggerDryRun
│   ├── streaming.ts                                # MODIFIED — handle new SSE card types
│   ├── agui-event-handler.ts                       # MODIFIED — route new card types
│   ├── components/
│   │   ├── message-area/
│   │   │   ├── workflow-card.tsx                   # MODIFIED — v2 with metadata, dry-run button, prereq warnings
│   │   │   ├── run-result-card.tsx                 # NEW — structured run result display
│   │   │   ├── dry-run-card.tsx                    # NEW — dry run result display
│   │   │   └── prereq-check-card.tsx              # NEW — prerequisite validation results
│   │   └── chat-response.tsx                       # MODIFIED — render new card types from message parts
│   ├── sidebar/
│   │   └── static-nav-section.tsx                 # MODIFIED — add workflow indicator badge
│   └── workflow-panel.tsx                          # NEW — slide-out panel for connected workflows
├── workflows/
│   ├── page.tsx                                    # MODIFIED — "Open in Chat" button navigating to /chat?conversationId=X
│   ├── types.ts                                    # MODIFIED — add conversationId to Workflow type
│   ├── api.ts                                      # MODIFIED — add getWorkflowConversation
│   └── components/
│       └── workflow-detail-view.tsx                # MODIFIED — "Open in Chat" action button
└── lib/
    └── hooks/
        └── use-workflow-run-updates.ts             # EXISTING — no changes needed
```

---

## 6. Implementation Phases

### Phase 1: Bidirectional Conversation–Workflow Link (Foundation)

**Goal**: Establish the data model link so conversations know their workflows and vice versa.

**Steps**:

1. **Conversation schema** — Add `connectedWorkflowIds: [String]` to `conversationSchema` and `IConversation` interface.

2. **Node.js internal routes** — Add `PATCH .../conversations/:id/workflows` (add/remove) and `GET .../conversations/:id/workflows` (list).

3. **IConversationWriter interface** — Create the ABC in `services/workflows/interface/conversation_writer.py` with `link_workflow()` / `unlink_workflow()` methods.

4. **NodeConversationWriter** — Implement `link_workflow()` / `unlink_workflow()` via HTTP PATCH to the new Node route.

5. **workflow_manage._create()** — After successful `TaskEngine.create()`, call `conversation_writer.link_workflow(conversation_id, workflow_id)`.

6. **workflow_manage cancel/delete** — On cancel/delete, call `conversation_writer.unlink_workflow()`.

**Files changed**: `conversation.schema.ts`, `conversation.interfaces.ts`, `workflows-internal.routes.ts`, `conversation_writer.py` (interface + adapter), `workflow_manage.py`

**Tests**:
- Unit: `NodeConversationWriter.link_workflow()` mocks HTTP, verifies correct PATCH payload
- Unit: `workflow_manage._create()` verifies `link_workflow` called with correct args
- Integration: Create workflow from chat → verify `Conversation.connectedWorkflowIds` contains the new workflow_id

---

### Phase 2: Enhanced Run Result Delivery to Chat

**Goal**: Workflow run results appear in chat as rich, structured cards instead of plain text.

**Steps**:

1. **Enhance RunResultMessage** — Add `workflow_name`, `error`, `is_dry_run`, `trigger_kind`, `started_at`, `completed_at` fields.

2. **Enhance Node internal route** — `POST /conversations/:id/messages` creates a `tool_call` message with `toolName: "workflow_run_result"` and structured payload instead of a plain `bot_response`.

3. **Enhance TaskExecutor._append_run_result_to_conversation()** — Populate the enriched `RunResultMessage` with `workflow_name` (from task), `trigger_kind`, timing, error info.

4. **Frontend RunResultCard** — New component rendering status badge, output summary, error (if failed), timing, and link to workflow detail.

5. **Frontend chat-response.tsx** — Detect `toolName: "workflow_run_result"` in message parts and render `RunResultCard`.

6. **Socket.IO updates** — Existing `workflowRunUpdate` events already carry `conversationId`; frontend already subscribes. Verify `RunResultCard` reacts to live status changes.

**Files changed**: `models.py` (RunResultMessage), `workflows-internal.routes.ts`, `executor.py`, `conversation_writer.py` (adapter), `run-result-card.tsx` (new), `chat-response.tsx`, `types.ts` (frontend)

**Tests**:
- Unit: `TaskExecutor._append_run_result_to_conversation()` sends correct enriched payload
- Unit: `RunResultCard` renders succeeded/failed/awaiting_input states correctly
- E2E: Create workflow → trigger run → verify RunResultCard appears in chat with correct status

---

### Phase 3: Conversation Workflow Panel

**Goal**: Users can see and manage all workflows connected to the current conversation from a side panel.

**Steps**:

1. **Frontend API** — `ConversationWorkflowsApi.getConnectedWorkflows(conversationId)`: calls `GET /api/v1/conversations/:id/workflows` → Node proxies to Python `GET /api/v1/workflows?ids=...` or direct Mongo+Python aggregation.

2. **Node.js route** — `GET /api/v1/conversations/:id/workflows` reads `connectedWorkflowIds` from the conversation, then fetches full workflow details from Python.

3. **WorkflowPanel component** — Slide-out panel triggered by a toolbar icon in chat header. Lists connected workflows with:
   - Name, status badge, execution kind
   - Trigger type icon + schedule description
   - Last run status + time
   - Next scheduled run time
   - Action buttons: Dry Run, Pause/Resume, View Runs, View in Dashboard

4. **Chat header integration** — Add a "Workflows" icon button to the chat header toolbar (visible only when `connectedWorkflowIds.length > 0`). Show a count badge.

5. **Real-time updates** — Socket.IO `workflowRunUpdate` events with `conversationId` update the panel's workflow status live.

**Files changed**: `chat/api.ts`, Node.js conversation routes, `workflow-panel.tsx` (new), chat header component, `notification.service.ts`

**Tests**:
- Unit: `WorkflowPanel` renders workflow list with correct metadata
- Unit: Panel updates live on `workflowRunUpdate` Socket.IO event
- E2E: Create 2 workflows in chat → open panel → verify both listed with correct metadata

---

### Phase 4: Prerequisite Validation Preview

**Goal**: Before creating a workflow, the agent can explicitly verify prerequisites and surface results to the user.

**Steps**:

1. **New `validate` action** in `workflow_manage` tool — Accepts the same parameters as `create` (connector_ids, collection_ids, toolset_ids, etc.) but only runs `PrerequisiteValidator.validate()` without creating anything. Returns structured `PrerequisiteCheckResult`.

2. **Schedule feasibility check** — Add validation that `fire_at` for `one_time` triggers is in the future. Add validation that `cron_expression` is valid and produces a next-run-at within a reasonable window.

3. **SSE card** — New `prerequisite_check_result` CUSTOM event emitted via a POST_TOOL_USE hook when `workflow_manage(action="validate")` succeeds. Payload: `{ issues: [{kind, id, reason, blocking}], ok, schedule_preview: [{kind, next_run_at}] }`.

4. **Frontend PrereqCheckCard** — Renders pass/fail per resource, with icons and explanatory text. Blocking issues shown in red, non-blocking in amber.

5. **Agent system prompt guidance** — Update `workflow_manage` tool description to instruct the model to call `validate` before `create` when connectors/collections are involved, and to show the user the results before proceeding.

**Files changed**: `workflow_manage.py`, `prereq_check_card.py` (new hook), `prereq-check-card.tsx` (new component), `chat-response.tsx`, `tasks_wiring.py`

**Tests**:
- Unit: `validate` action returns correct issues for missing connector
- Unit: `validate` action rejects `fire_at` in the past
- Unit: `PrereqCheckCard` renders blocking/non-blocking issues correctly
- E2E: Ask agent to create workflow with expired connector → verify prereq card shows error

---

### Phase 5: In-Chat Dry Run

**Goal**: Users can trigger a dry run from the chat conversation where the workflow was created, and see results inline.

**Steps**:

1. **New `dry_run` action** in `workflow_manage` tool — Calls `TaskEngine.dry_run()`, returns `{ workflow_id, run_id, status: "pending", is_dry_run: true }`.

2. **SSE card** — New POST_TOOL_USE hook emits `workflow_dry_run_started` CUSTOM event with `{ workflowId, runId, title }`.

3. **Frontend DryRunCard** — Shows "Dry run started..." with a spinner, then transitions via Socket.IO `workflowRunUpdate` to show results (succeeded/failed + output/error). Visual differentiation from real runs (dashed border, "DRY RUN" badge).

4. **WorkflowPanel dry-run button** — Each workflow in the panel has a "Dry Run" action that calls `WorkflowsApi.dryRun(workflowId)` and shows a toast + the DryRunCard in the chat.

5. **WorkflowCard v2 dry-run button** — The inline WorkflowCard gains a "Dry Run" action button.

6. **Suppress notifications for dry runs** — Already implemented in `TaskExecutor._notify()` which checks `run.is_dry_run`.

**Files changed**: `workflow_manage.py`, `workflow_dry_run_card.py` (new hook), `dry-run-card.tsx` (new component), `workflow-card.tsx` (add button), `workflow-panel.tsx` (add button), `chat-response.tsx`

**Tests**:
- Unit: `dry_run` action calls `TaskEngine.dry_run()` correctly
- Unit: Dry run does not trigger notifications (existing — verify)
- Unit: `DryRunCard` shows spinner then transitions to result
- E2E: Create workflow → click "Dry Run" → verify DryRunCard appears and shows result

---

### Phase 6: Workflow ↔ Chat Navigation

**Goal**: Clicking a workflow anywhere navigates to its originating chat. Clicking "Workflows" in chat sidebar shows connected workflows.

**Steps**:

1. **Workflow model enhancement** — Add `conversationId` to the frontend `Workflow` type. Python `Workflow` model already has `created_from_conversation_id` on the underlying `TaskDefinition`.

2. **Python REST routes** — `GET /api/v1/workflows/:id` response includes `conversationId` (mapped from `task.created_from_conversation_id`).

3. **Workflow detail view** — Add "Open in Chat" button when `conversationId` exists. Navigates to `/chat?conversationId=<id>`.

4. **Workflow list page** — Add "Open in Chat" action in the row action menu (when `conversationId` exists).

5. **Chat sidebar** — In the static nav section, show a "Workflows" count badge next to the chat title when `connectedWorkflowIds.length > 0`, clicking opens the WorkflowPanel.

6. **Notification redirect** — `TaskExecutor._notify()` already sets `redirect_link` to `/chat?conversationId=<cid>` when available. Frontend notification click handler already uses this. Verify end-to-end.

**Files changed**: `workflow-detail-view.tsx`, `page.tsx` (workflows), `types.ts` (workflows), `api.ts` (workflows), Python `_task_to_workflow()`, Python workflow REST routes, chat sidebar components

**Tests**:
- Unit: `Workflow` response includes `conversationId` when present
- E2E: Create workflow in chat → go to /workflows → click row → verify "Open in Chat" button → navigates back to correct conversation

---

## 7. Interface & Abstraction Summary

### 7.1 Python Interfaces (Ports)

```python
# services/workflows/interface/conversation_writer.py
class IConversationWriter(ABC):
    async def append_result(self, conversation_id, org_id, msg: RunResultMessage) -> None: ...
    async def link_workflow(self, conversation_id, org_id, workflow_id) -> None: ...
    async def unlink_workflow(self, conversation_id, org_id, workflow_id) -> None: ...

# services/tasks/interface/notifier.py (existing, no changes)
class ITaskNotifier(ABC):
    async def notify(self, notification: TaskNotification) -> None: ...

# services/tasks/interface/task_store.py (existing, no changes)
class ITaskStore(ABC):
    async def create(self, task: TaskDefinition) -> TaskDefinition: ...
    async def get(self, task_id, org_id) -> TaskDefinition | None: ...
    async def update(self, task, expected_revision) -> TaskDefinition: ...
    async def delete(self, task_id, org_id) -> bool: ...
    async def list(self, query: TaskQuery) -> Page[TaskDefinition]: ...
```

### 7.2 Frontend Interfaces

```typescript
// Conversation workflow link
interface ConversationWorkflowsApi {
  getConnectedWorkflows(conversationId: string): Promise<Workflow[]>;
  triggerDryRun(workflowId: string): Promise<{ runId: string }>;
}

// Enhanced SSE card payloads
interface RunResultCardPayload {
  name: 'workflow_run_result';
  workflowId: string;
  workflowName: string;
  runId: string;
  status: WorkflowRunStatus;
  outputSummary?: string;
  error?: string;
  isDryRun: boolean;
  triggerKind?: string;
  startedAt?: string;
  completedAt?: string;
  redirectLink: string;
}

interface PrereqCheckCardPayload {
  name: 'prerequisite_check_result';
  ok: boolean;
  issues: Array<{
    kind: string;      // "connector" | "collection" | "toolset" | "mcp_server"
    id: string;
    reason: string;
    blocking: boolean;
  }>;
  schedulePreview?: Array<{
    kind: string;       // "cron" | "one_time" | "interval"
    nextRunAt?: string;
  }>;
}
```

---

## 8. Data Flow Diagrams

### 8.1 Workflow Creation (Enhanced)

```
User: "Schedule a daily Jira digest at 9am"
                │
                ▼
         Agent (LLM) decides to call workflow_manage(action="validate", ...)
                │
                ▼
    PrerequisiteValidator.validate() ──────────► SSE: prerequisite_check_result card
                │                                          │
                ▼ (if ok)                                  ▼
         Agent shows user: "Prerequisites OK,        Frontend renders
         schedule: daily at 9:00 AM UTC.              PrereqCheckCard ✓
         Shall I create it?"
                │
                ▼ (user confirms)
         Agent calls workflow_manage(action="create", ...)
                │
                ▼
    TaskEngine.create() ─────────────────► ArangoDB: TaskDefinition
         │                                 Redis: TaskTrigger
         │                                 Redis: idempotency
         ▼
    conversation_writer.link_workflow() ──► Node PATCH: Conversation.connectedWorkflowIds.push(wf_id)
         │
         ▼
    SSE: workflow_created card ────────────► Frontend renders WorkflowCard v2
                                              (with metadata badges, dry-run button)
```

### 8.2 Workflow Run → Chat Result

```
SchedulerLoop / fire_trigger ──► Kafka: task_run_dispatch
                                          │
                                          ▼
                                    TaskExecutor.handle_dispatch()
                                          │
                                          ▼
                                    _execute_claimed_run()
                                          │
                                          ▼
                                    Agent.run(goal) or CodeWorkflowRunner.run()
                                          │
                                          ▼
                                    _finalize_result()
                                     │           │
                                     │           ▼
                                     │     _notify() ──► Kafka: notification
                                     │                          │
                                     ▼                          ▼
                          _append_run_result_to_conversation()  NotificationConsumer
                                     │                          │
                                     ▼                          ▼
                          NodeConversationWriter                emitWorkflowRunUpdate()
                          .append_result()                      │
                                     │                          ▼
                                     ▼                    Socket.IO → Frontend
                          Node POST /conversations/:id/messages
                                     │
                                     ▼
                          Conversation.messages.push({
                            messageType: 'tool_call',
                            tools: [{ toolName: 'workflow_run_result', toolResult: { ... } }]
                          })
                                     │
                                     ▼
                          Frontend poll / next conversation load
                          → renders RunResultCard
```

### 8.3 Dry Run from Chat

```
User: "Run a dry test of the Jira digest workflow"
                │
                ▼
         Agent calls workflow_manage(action="dry_run", workflow_id="...")
                │
                ▼
    TaskEngine.dry_run() ──► Redis: TaskRun(is_dry_run=True)
         │                   Kafka: task_run_dispatch
         ▼
    SSE: workflow_dry_run_started card ──► Frontend renders DryRunCard (spinner)
                                                     │
                                              Socket.IO: workflowRunUpdate
                                              (status: "running" → "succeeded")
                                                     │
                                                     ▼
                                              DryRunCard updates to show result
                                              (with output summary / error)
```

---

## 9. Testing Strategy

### 9.1 Unit Tests

| Area | Test | Priority |
|------|------|----------|
| `NodeConversationWriter.link_workflow()` | Mocks httpx, verifies correct PATCH URL/payload/auth | P0 |
| `NodeConversationWriter.append_result()` | Verifies enriched payload shape | P0 |
| `WorkflowManageTool._dry_run()` | Calls `TaskEngine.dry_run()` with correct params, returns expected ToolOutput | P0 |
| `WorkflowManageTool._validate()` | Runs `PrerequisiteValidator.validate()` without side effects | P0 |
| `WorkflowManageTool._create()` | Verifies `link_workflow` called after successful create | P0 |
| `WorkflowManageTool._create()` | Verifies `link_workflow` failure doesn't fail the create | P1 |
| `task_scheduled_card.py` | Verifies enhanced card payload includes metadata badges data | P1 |
| `PrereqCheckCard` (React) | Renders blocking/non-blocking issues, all-pass state | P0 |
| `RunResultCard` (React) | Renders succeeded/failed/awaiting states, dry-run badge | P0 |
| `WorkflowPanel` (React) | Renders list of workflows, handles empty state, live updates | P1 |
| `WorkflowCard v2` (React) | Renders metadata badges, dry-run button, prereq warnings | P1 |

### 9.2 Integration / E2E Tests

| Scenario | Steps | Expected Outcome |
|----------|-------|------------------|
| Workflow created → visible in conversation | 1. Send chat message requesting workflow 2. Agent creates workflow | WorkflowCard appears in chat, conversation.connectedWorkflowIds contains workflow_id |
| Run result appears in chat | 1. Create workflow with one_time trigger 2. Wait for scheduled fire 3. Check conversation messages | RunResultCard in conversation messages with correct status |
| Prerequisite check blocks creation | 1. Ask agent to create workflow with disconnected connector | PrereqCheckCard shows blocking issue, agent does not create |
| Dry run from chat | 1. Create workflow 2. Ask agent to dry-run it | DryRunCard appears, transitions to succeeded, no notification emitted |
| Navigate workflow → chat | 1. Create workflow in chat 2. Go to /workflows 3. Click "Open in Chat" | Navigates to /chat?conversationId=X with correct conversation |
| Workflow panel shows metadata | 1. Create 2 workflows in same conversation 2. Open workflow panel | Panel lists both with triggers, status, last run, next run |

### 9.3 Edge Cases

| Edge Case | Expected Behavior |
|-----------|-------------------|
| Conversation deleted before run completes | `NodeConversationWriter` gets 404, logs warning, run completes normally |
| Workflow created outside of any conversation (REST API) | `created_from_conversation_id` is None; no link_workflow call; notification redirects to /workflows |
| Multiple workflows in same conversation, one cancelled | `unlink_workflow` removes only the cancelled one; panel shows remaining |
| Dry run on a workflow with expired connector | Dry run fails with prerequisite error; DryRunCard shows error message |
| Conversation loaded with stale connectedWorkflowIds (workflow deleted) | Frontend `getConnectedWorkflows` filters out 404 responses gracefully |
| `fire_at` in the past for one_time trigger | `validate` action catches it; if user bypasses and calls `create`, trigger fires immediately (existing behavior) with a warning |

---

## 10. Security Considerations

1. **Internal routes remain scoped-JWT-gated** — All `PATCH/POST` to `workflows/internal/conversations/*` require the `pipeshub-node-internal` audience JWT. No user-facing token can hit these routes.

2. **Org isolation on link/unlink** — The PATCH route verifies `org_id` from the JWT matches the conversation's `orgId`. A workflow from org A cannot be linked to a conversation in org B.

3. **No workflow_id guessing** — `link_workflow` is only called from `workflow_manage._create()` which already verifies the caller owns the conversation (via `context.conversation_id`).

4. **Dry run respects permissions** — `TaskEngine.dry_run()` calls `self.get(task_id, org_id)` which 404s across orgs.

5. **Validate action is read-only** — `workflow_manage(action="validate")` never creates or mutates anything. It's safe to expose without write permissions.

---

## 11. Performance Considerations

1. **Conversation document growth** — `connectedWorkflowIds` is bounded by the number of workflows a user creates in a single conversation (typically 1-3). No pagination needed.

2. **WorkflowPanel fetches** — One REST call per panel open (not per render). Cached client-side until a `workflowRunUpdate` socket event arrives with a matching `conversationId`.

3. **Run result append** — Single MongoDB `$push` operation per run completion. No N+1.

4. **PrerequisiteValidator** — Already makes batch ArangoDB queries per call. No new queries added.

5. **SSE card events** — Lightweight JSON payloads (< 1KB each). No new streaming overhead.

---

## 12. Maintainability & Extensibility

1. **IConversationWriter abstraction** — Future backends (e.g., direct ArangoDB conversation store) only need to implement the port; TaskExecutor and WorkflowManageTool never change.

2. **Card system** — New card types (e.g., "workflow version deployed") only require a new React component registered in `CARD_REGISTRY` and a new SSE event name. No plumbing changes.

3. **Workflow panel actions** — New actions (e.g., "Edit schedule", "View logs") are additive to the panel's action list. Each is a self-contained button→API call→toast pattern.

4. **Phase-gated rollout** — Each phase is independently deployable and behind the existing `PIPESHUB_ENABLE_TASKS` feature flag. No big-bang migration.

---

## 13. Open Questions / Risks

| # | Question | Recommended Resolution |
|---|----------|----------------------|
| 1 | Should `connectedWorkflowIds` be denormalized into the frontend's conversation list API response? | No — fetch on demand when opening a conversation or panel. Avoids N+1 on conversation list. |
| 2 | Should dry-run output be persisted as a conversation message? | Yes — same as regular run results, but marked with `is_dry_run: true`. Users may want to reference dry-run output later. |
| 3 | What happens when a workflow is created via REST API (not chat)? | No `conversation_id` → no link. Workflow is only visible in `/workflows` dashboard. This is correct. |
| 4 | Should `unlink_workflow` be called on `cancel` or only on `delete`? | On both `cancel` and `delete` — a cancelled workflow should not appear in the panel as "connected". The link is to active/paused workflows. |
| 5 | Multi-tab consistency: user has chat open in two tabs | Socket.IO delivers to all tabs via room-based routing (existing). Both tabs see the same `workflowRunUpdate`. Panel refresh is triggered by socket event. |
