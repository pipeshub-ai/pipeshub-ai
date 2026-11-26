import type { ConversationRunState } from '@/lib/contracts/harness/generated/ext/pipeshub/api/conversation-run-state';
import type { CitationOrigin } from "./components/message-area/response-tabs/citations";
import type { CitationMaps, StreamingCitationData } from "./components/message-area/response-tabs/citations/types";
import type { ThreadMessageLike } from "@assistant-ui/react";
import {
  ACCEPTED_MIME_TYPES,
  SUPPORTED_FILE_TYPES,
} from "./utils/attachment-file-types";
import type { CutShortReason, InteractionDecision, RuleLayer } from '@/lib/contracts/harness/generated/core/enums';
import type { TurnGatesRequest } from './run-gates';
import type { ResolvedRunConfig } from '@/lib/contracts/control-plane/generated/resolved-run-config';
import type { RepoActivity } from './repository-steps';

// Chat types following project conventions

// Confidence level for bot responses
export type ConfidenceLevel = "Very High" | "High" | "Medium" | "Low";

// Tab types for response view
export type ResponseTab = "answer" | "sources" | "citation";

/**
 * Platform-normalized reasoning effort levels. `null`/absent means "no
 * explicit user choice" — the backend applies the model's own default
 * effort, else `DEFAULT_REASONING_EFFORT` ("high"), for any reasoning-capable
 * model rather than deferring to the provider's own default.
 *
 * `'none'` is kept in the type for backward compatibility with
 * already-persisted conversations/agents, but is no longer offered as a
 * user choice (see `REASONING_EFFORT_OPTIONS` in `model-selector-panel.tsx`):
 * fully disabling reasoning made some models prone to hallucinating
 * malformed tool-call names. The backend silently upgrades an incoming
 * `'none'` to `'low'` instead of honoring it — see `_reasoning_effort_kwargs`
 * in `app/utils/aimodels.py`.
 */
export type ReasoningEffort = "none" | "low" | "medium" | "high" | "max";

/**
 * Effective reasoning effort applied by the backend (`_reasoning_effort_kwargs`
 * in `backend/python/app/utils/aimodels.py`) when neither the user, the agent
 * nor the model set one. Kept in sync with `DEFAULT_REASONING_EFFORT` there.
 */
export const DEFAULT_REASONING_EFFORT: ReasoningEffort = "high";

/**
 * What the backend actually floors a legacy `'none'` to
 * (`REASONING_MANDATORY_FALLBACK_EFFORT` in `app/utils/llm_api_mode_store.py`).
 */
const REASONING_MANDATORY_FALLBACK_EFFORT: ReasoningEffort = "low";

/**
 * Maps a persisted/legacy `'none'` to the value the backend actually runs
 * with, so every UI surface (toolbar label, radio selection, regen payload)
 * agrees with server behavior instead of echoing a choice that's no longer offered.
 */
export function normalizeReasoningEffort(
  value: ReasoningEffort | null,
): ReasoningEffort | null {
  return value === "none" ? REASONING_MANDATORY_FALLBACK_EFFORT : value;
}

// Inline citation for display within answer text
export interface InlineCitation {
  id: string;
  label: string; // e.g., "Sales 1", "Gmail 3"
  sourceType: string; // For icon/color mapping (slack, gmail, jira, etc.)
  sourceId?: string; // Reference to source for scroll targeting
}

export interface ChatMessage {
  id: string;
  conversationId: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  sources?: ChatSource[];
}

export interface ChatSource {
  id: string;
  title: string;
  type: string;
  url?: string;
  summary?: string;
  category?: string;
  topics?: string[];
  citationLabel?: string; // e.g., "Sales 1" for inline citation display
}

export interface ModelInfo {
  modelKey: string;
  modelName: string;
  /**
   * Main assistant: often `quick`, or `agent:<segment>` when restoring agent-style modes.
   * **Agent** conversations: API uses plain `auto` | `quick` | `planExecute` | `deep` (no `agent:`
   * prefix); legacy conversations may still carry `verification` (pre-rename alias for `planExecute`).
   */
  chatMode: string;
  modelFriendlyName?: string;
  /** Restored from the conversation's saved modelInfo when loading a thread. */
  reasoningEffort?: ReasoningEffort;
  modelProvider?: string;
  /** On an answer, a short note the AI service added about the model that answered. */
  modelNote?: string;
}

/** Entry in the sharedWith array from conversation API responses */
export interface SharedWithEntry {
  userId: string;
  accessLevel: string;
  _id: string;
}

/** Initiator of a chat, present on conversations shared with the current user */
export interface SharedByInfo {
  userId: string;
  name: string;
}

/**
 * Pagination metadata returned by the conversation detail API.
 * `hasNextPage` means there are older message batches to load;
 * the API sorts messages newest-first so page 1 = most recent.
 */
/** Where a forked conversation was copied from (GET conversation detail `forkedFrom`). */
export interface ForkOrigin {
  conversationId: string;
  messageId: string;
  /** `null` when the viewer cannot open the original (deleted, archived, or not theirs). */
  title: string | null;
  /** This chat's copy of the fork point; the "forked from here" line goes after it. */
  atMessageId?: string;
}

/** A chat the viewer made from this one (GET conversation detail `forks`). */
export interface ForkChild {
  conversationId: string;
  title: string;
  /** The message of this chat the fork was made at; the link goes after it. */
  fromMessageId: string;
}

export interface ConversationPagination {
  page: number;
  limit: number;
  totalCount: number;
  totalPages: number;
  hasNextPage: boolean;
  hasPrevPage: boolean;
}

export interface ConversationApiResponse {
  harnessState?: ConversationRunState;
  _id: string;
  userId: string;
  orgId: string;
  title: string;
  initiator: string;
  isShared: boolean;
  isDeleted: boolean;
  isArchived: boolean;
  lastActivityAt: number;
  status: string;
  modelInfo: ModelInfo;
  sharedWith: SharedWithEntry[];
  /** API may return string[] or structured error objects */
  conversationErrors?: unknown[];
  createdAt: string;
  updatedAt: string;
  isOwner: boolean;
  accessLevel: string;
  /** `true` when the owner pinned it; absent (never `false`) otherwise. Owner's own list only. */
  isPinned?: boolean;
  /** Present when this conversation is linked to a Project. */
  projectId?: string;
  projectVisibility?: 'private' | 'project';
  sharedBy?: SharedByInfo;
  /** Only on agent conversations shared with the user; needed to open them (`?agentId=`). */
  agentKey?: string;
}

export type ConversationSource = "owned" | "shared";

export interface ConversationsListResponse {
  conversations: ConversationApiResponse[];
  source: ConversationSource;
  pagination: {
    page: number;
    limit: number;
    totalCount: number;
    totalPages: number;
    hasNextPage: boolean;
    hasPrevPage: boolean;
  };
}

export interface Conversation {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  isShared: boolean;
  sharedWith: SharedWithEntry[];
  lastActivityAt?: number;
  status?: string;
  modelInfo?: ModelInfo;
  isOwner?: boolean;
  /** Pinned by its owner. Absent when not pinned; only set on the owner's own list. */
  isPinned?: boolean;
  /** Present when this conversation is linked to a Project. */
  projectId?: string;
  projectVisibility?: 'private' | 'project';
  sharedBy?: SharedByInfo;
  /** Set on a shared agent conversation: the agent it belongs to. */
  agentKey?: string;
  /** ADR-UI-01 D2: the chat's run state for the caller (working, needs you, finished and not seen); harness only. */
  harnessState?: ConversationRunState;
}

export interface ChatSuggestion {
  id: string;
  text: string;
  icons: Array<
    "slack" | "jira" | "notion" | "sheets" | "confluence" | "github"
  >;
}

/**
 * Top-level UI mode toggle (Internal Search vs Chat/Web).
 *
 * This is **NOT** the `chatMode` field in stream request payloads — that is
 * {@link StreamChatModePayload}. Code that handles `settings.mode` must not
 * import or assign `StreamChatModePayload` values here.
 */
export type ChatMode = "chat" | "search";

/**
 * Query sub-modes selectable from the dropdown panel.
 * These map to API `chatMode` for assistant streams.
 */
export type QueryMode = "chat" | "web-search" | "agent";

/** Strategy when query mode is Agent (toolbar dropdown). */
export type AgentStrategy = "auto" | "quick" | "plan-execute" | "deep";

/**
 * Segment after `agent:` in the stream API body (differs from {@link AgentStrategy} for plan-execute → planExecute).
 */
export type AgentStrategyApiSegment = "auto" | "quick" | "planExecute" | "deep";

/**
 * API `chatMode` for streams (assistant modes + agent strategy variant).
 * The assistant's values are the ones its routes accept (`assistantChatModeSchema`
 * on the server): a stream and a regenerate refuse anything else.
 */
export type StreamChatModePayload =
  | "web_search"
  | "internal_search"
  | "agent"
  | `agent:${AgentStrategyApiSegment}`;

/** Maps UI agent strategy to the API `agent:` segment (plan-execute → planExecute). */
export function agentStrategyToApiSegment(
  strategy: AgentStrategy,
): AgentStrategyApiSegment {
  if (strategy === "plan-execute") return "planExecute";
  return strategy;
}

/**
 * Segments accepted by agent stream/regenerate HTTP bodies (`chatMode` field).
 * Only the current canonical segments — outgoing requests always go through
 * {@link agentStrategyToApiSegment}, which never produces the legacy
 * `verification` wire value; restoring THAT from persisted history is
 * `apply-conversation-model-info.ts`'s job, not this outgoing-request path.
 */
const AGENT_HTTP_CHAT_MODES: readonly AgentStrategyApiSegment[] = [
  "auto",
  "quick",
  "planExecute",
  "deep",
];

/**
 * Map internal {@link StreamChatModePayload} to the plain `chatMode` string
 * expected by POST `/api/v1/agents/.../stream` (Python agent routes).
 */
export function streamChatModeToAgentApiChatMode(
  chatMode: StreamChatModePayload,
): AgentStrategyApiSegment {
  if (typeof chatMode === "string" && chatMode.startsWith("agent:")) {
    const rest = chatMode.slice(6) as AgentStrategyApiSegment;
    if (AGENT_HTTP_CHAT_MODES.includes(rest)) return rest;
  }
  return "auto";
}

/** Configuration for a single query mode option in the selector panel. */
export interface QueryModeConfig {
  id: QueryMode;
  label: string;
  /** Short label for the bottom toolbar (e.g. "Chat", "Image") */
  toolbarLabel: string;
  description: string;
  /** Icon identifier — Material Icon name or SVG path (see iconType) */
  icon: string;
  /** How to render the icon */
  iconType: "material" | "svg" | "component";
  /** CSS variable references for this mode's color scheme */
  colors: {
    bg: string; // Background for active state
    fg: string; // Text/label color
    icon: string; // Icon color
    toggle: string; // Active toggle/indicator color
  };
  /** Whether this mode is currently supported by the backend */
  enabled: boolean;
}

/**
 * Assistant chat knowledge scope — **same shape as stream/search `filters`**.
 *
 * - **`apps`** — whole apps: connectors and collections.
 * - **`kb`** — older name for collection ids; read, no longer written by the picker.
 * - **`recordGroups`** — record groups, each with the groups nested in it and their records.
 * - **`records`** — folders and records, each with everything under it.
 *
 * Only the topmost selected node is kept: selecting a node drops what was
 * selected under it (see `utils/tree-selection.ts`).
 */
export interface ChatKnowledgeFilters {
  apps: string[];
  kb: string[];
  recordGroups?: string[];
  records?: string[];
  /**
   * Records selected alone, without what is under them. The picker has no
   * control for it; an API client can start a conversation with it, and the
   * conversation keeps it when it is opened here.
   */
  recordsExact?: string[];
}

/** What the chat UI remembers about a node it has listed or selected. */
export interface CollectionMeta {
  name: string;
  nodeType: string;
  connector: string;
  /** Ids of the nodes above it in the picker tree, root first (absent for a root). */
  ancestorIds?: string[];
  extension?: string | null;
  mimeType?: string | null;
  /** The place lookup was refused: deleted, or not readable by this user. */
  unavailable?: boolean;
}

export interface AppliedFilterNode {
  id: string;
  name: string;
  nodeType: string;
  connector: string;
  extension?: string;
  mimeType?: string;
  /** Named in the question with @. Absent on messages saved before this was kept. */
  mentioned?: boolean;
}

export interface AppliedFilters {
  apps: AppliedFilterNode[];
  kb: AppliedFilterNode[];
  recordGroups?: AppliedFilterNode[];
  records?: AppliedFilterNode[];
  recordsExact?: AppliedFilterNode[];
}

/** Shallow copy for stream/search payloads (keeps a stable object shape for callers). */
export function buildAssistantApiFilters(filters: ChatKnowledgeFilters): ChatKnowledgeFilters {
  return {
    apps: [...(filters.apps ?? [])],
    kb: [...(filters.kb ?? [])],
    ...(filters.recordGroups?.length ? { recordGroups: [...filters.recordGroups] } : {}),
    ...(filters.records?.length ? { records: [...filters.records] } : {}),
    ...(filters.recordsExact?.length ? { recordsExact: [...filters.recordsExact] } : {}),
  };
}

/** Attached on send for replay + UI; `kind` defaults to `collectionRoot` when absent (legacy). */
export type ChatCollectionAttachmentKind = "collectionRoot" | "recordGroup";

export interface ChatCollectionAttachment {
  id: string;
  name: string;
  kind?: ChatCollectionAttachmentKind;
}

/**
 * Per-agent-mode capability toggles.
 *
 * - `internalSearch` gates knowledge tools (retrieval, knowledgegraph, knowledgehub),
 *   and disables the Connectors + Collections tabs in the UI when false.
 * - `webSearch` gates web_search + fetch_url tools.
 *
 * When absent from a stream request, the backend defaults both to `true`
 * (preserving today's behavior for existing clients).
 */
export interface AgentCapabilities {
  internalSearch: boolean;
  webSearch: boolean;
}

/** Default capabilities — both search modes enabled. */
export const DEFAULT_AGENT_CAPABILITIES: AgentCapabilities = {
  internalSearch: true,
  webSearch: true,
};

export interface ChatSettings {
  mode: ChatMode;
  queryMode: QueryMode;
  /** Used when queryMode is 'agent'. */
  agentStrategy: AgentStrategy;
  filters: ChatKnowledgeFilters;
  /**
   * Capability toggles for universal agent mode (main chat, no agentId).
   * Persisted to localStorage key `pipeshub-agent-capabilities`.
   */
  agentCapabilities: AgentCapabilities;
  /** The "New agent harness" switch (CTL-7): how a new conversation starts. Persisted per browser. */
  newHarness: boolean;
  /**
   * Per-context map of the model explicitly chosen by the user in the model
   * selector panel. The key is either ASSISTANT_CTX (for the non-agent chat) or
   * an agentId string. A missing/null value means "use defaultModels[ctxKey]".
   *
   * Keeping selections scoped per context prevents leaks across assistant and
   * agents that have different model configurations.
   */
  selectedModels: Record<string, ModelOverride | null>;
  /**
   * Per-context default model (API `isDefault: true`). Populated when models
   * are loaded for that context.
   */
  defaultModels: Record<string, ModelOverride | null>;
  /**
   * Per-context cache of the full model list, with a freshness timestamp.
   * Used for deduping fetches and for invalidating stale selections.
   */
  availableModels: Record<
    string,
    { models: AvailableLlmModel[]; fetchedAt: number }
  >;
  /**
   * Per-context reasoning effort override, keyed the same way as
   * `selectedModels`. `null`/missing means "use the model's own default".
   * Only meaningful when the selected model has `isReasoning: true`.
   */
  reasoningEffort: Record<string, ReasoningEffort | null>;
  /**
   * Per-agent default reasoning effort from the agent's config. Populated by
   * `fetchModelsForContext` when loading an agent. Used as the fallback when
   * the user hasn't set an explicit override.
   */
  agentDefaultReasoningEffort: Record<string, ReasoningEffort | null>;
  /**
   * What a new assistant chat opens on when the edition sets one. A stored
   * pick still wins, and a project chat keeps the concrete default model.
   */
  newChatDefaultModel: ModelOverride | null;
}

/**
 * Per-attachment upload state. Uploads start the moment the user adds the
 * file to the composer (not at send time), so each chip carries its own
 * status. Send is blocked until every chip is `'uploaded'` (or removed).
 */
export type AttachmentUploadStatus = "uploading" | "uploaded" | "error";

/**
 * How a chip entered the composer. `'paste-text'` marks a large plain-text
 * clipboard paste that was auto-converted into a synthetic `.txt` file (see
 * `utils/paste-attachment.ts`) — used to render a specialized chip/preview
 * instead of the generic file chip. The others are informational only.
 */
export type UploadedFileSource = "upload" | "drag" | "paste" | "paste-text";

export interface UploadedFile {
  id: string;
  file: File;
  name: string;
  size: number;
  type: string;
  preview?: string;
  status: AttachmentUploadStatus;
  /** Server-assigned ref, present once status === 'uploaded'. */
  ref?: AttachmentRef;
  /** User-facing message when status === 'error'. */
  errorMessage?: string;
  /** Origin of this chip — see {@link UploadedFileSource}. */
  source?: UploadedFileSource;
  /**
   * A file already stored with the question being edited, not one picked now. Removing the chip
   * only detaches it from the edit: the stored file stays, since a fork of the chat may use it.
   */
  existing?: boolean;
  /** First-line excerpt shown on the chip, present only when `source === 'paste-text'`. */
  pastePreview?: string;
  /** Character count of the original pasted text (present only when `source === 'paste-text'`). */
  pasteCharCount?: number;
  /** Line count of the original pasted text (present only when `source === 'paste-text'`). */
  pasteLineCount?: number;
}

/** Derived from `SUPPORTED_FILE_TYPES`/`ACCEPTED_MIME_TYPES` in `utils/attachment-file-types.ts`
 * (the single source of truth) so the two allowlists cannot drift apart. */
export type SupportedFileType = (typeof SUPPORTED_FILE_TYPES)[number];

/** Returned by the attachment upload endpoint; forwarded verbatim in the SSE stream body. */
export interface AttachmentRef {
  recordId: string;
  recordName: string;
  mimeType: string;
  extension: string;
  virtualRecordId: string;
  /**
   * Origin metadata, mirrored from `UploadedFileSource` when known
   * ('upload' | 'paste-text'). Deliberately not the bare 'paste' of
   * `UploadedFileSource`, which means a pasted image/file. Not currently
   * populated by the
   * upload API — detection of pasted-text attachments instead relies on
   * the `pasted-text-<timestamp>.txt` filename convention (see
   * `isPastedTextAttachment` in `utils/paste-attachment.ts`), matching the
   * same defense-in-depth pattern used server-side. Reserved for future
   * use once the upload pipeline threads real origin metadata through.
   */
  source?: "upload" | "paste-text";
  /** Set by the server once the upload was erased with another conversation that carried it. */
  removed?: boolean;
}

/** MIME types accepted by the chat attachment upload endpoint. */
export const CHAT_ATTACHMENT_ACCEPTED_MIMETYPES =
  Object.keys(ACCEPTED_MIME_TYPES);

/** Maximum file size for a single chat attachment (5 MB). */
export const CHAT_ATTACHMENT_MAX_BYTES = 5 * 1024 * 1024;

/** Maximum number of attachments per message. */
export const CHAT_ATTACHMENT_MAX_FILES = 10;

/**
 * How long one attachment upload may take. The server parses the file and has
 * a vision model describe its images before it answers, which can outlast the
 * API client's 90s default on a scanned or image-heavy PDF.
 */
export const CHAT_ATTACHMENT_UPLOAD_TIMEOUT_MS = 180_000;

/**
 * AG-UI wire event type discriminators (`data.type` on every AG-UI frame —
 * see `backend/python/app/agents/agent_loop/protocol/agui.py::AGUIEventType`
 * and the Node-side mirror in `backend/nodejs/.../utils/agui.ts`).
 * Consumed exclusively by `agui-event-handler.ts`. The frontend always
 * negotiates `protocol: 'agui'` (see `api.ts::runChatStream`), so this is
 * the only wire vocabulary the client needs to parse.
 */
export type AGUIWireEventType =
  | "RUN_STARTED"
  | "RUN_FINISHED"
  | "RUN_ERROR"
  | "STEP_STARTED"
  | "STEP_FINISHED"
  | "TEXT_MESSAGE_START"
  | "TEXT_MESSAGE_CONTENT"
  | "TEXT_MESSAGE_END"
  | "REASONING_START"
  | "REASONING_MESSAGE_START"
  | "REASONING_MESSAGE_CONTENT"
  | "REASONING_MESSAGE_END"
  | "REASONING_END"
  | "TOOL_CALL_START"
  | "TOOL_CALL_ARGS"
  | "TOOL_CALL_END"
  | "TOOL_CALL_RESULT"
  | "STATE_DELTA"
  | "STATE_SNAPSHOT"
  | "CUSTOM"
  | "HEARTBEAT";

/** One JSON Patch (RFC 6902) operation, as carried by AG-UI `STATE_DELTA.delta`. */
export interface AGUIJsonPatchOp {
  op: string;
  path: string;
  value?: unknown;
}

/** Envelope every AG-UI frame's `data` field satisfies (fields beyond `type` vary per event). */
export interface AGUIEventEnvelope {
  type: AGUIWireEventType;
  runId?: string;
  parentRunId?: string;
  [key: string]: unknown;
}

/** Single option in an ask_user_question tool payload. */
export interface AskUserQuestionOption {
  id: string;
  label: string;
  isUserInput: boolean;
  /** What happens if this option is picked. */
  description?: string;
}

export interface AskUserQuestionItem {
  uuid: string;
  question: string;
  options: AskUserQuestionOption[];
  multiSelect: boolean;
  /** Short label shown above the question. */
  header?: string;
}

export interface AskUserQuestionPayload {
  name: "ask_user_question";
  userIntent?: string;
  questions: AskUserQuestionItem[];
  /** ISO time after which the card can no longer be answered with its options. */
  expiresAt?: string;
  /** Set on this client when the user typed a reply in the chat instead of answering. */
  repliedInChat?: boolean;
  /** ADR-RUN-03 D1 (FU-2): a grounding question's typed fields, which the card words in the reader's language. */
  grounding?: GroundingCard;
}

/**
 * A grounding question (the subject the request names was not found): the subject, the near matches it offers and
 * the English labels of the card's own options, which a reply still sends (the worker reads it by them).
 */
export interface GroundingCard {
  subject: string;
  near: string[];
  useFound: string;
  nameIt: string;
  /** The connected sources searched besides the person's PipesHub knowledge, named with it. */
  places: string[];
}

export interface SSEAskUserQuestionEvent {
  status: string;
  toolData: AskUserQuestionPayload;
}

/** A source the run read, as the approval card names it: title and link, never an id on screen (UX1). */
export interface ApprovalSource {
  title?: string;
  recordId?: string;
  url?: string;
}

/**
 * Why the run asks: the rule and whose it is (QA6-F6). For the flow rule
 * (UX1) the run read private (`private`) or unverified (`unknown`) content
 * earlier in the conversation and this call would send content outside; a
 * layer's own approval rule says nothing of what the run read.
 */
export interface ApprovalReason {
  rule: string;
  /** Absent for a reason journaled before layers were named. */
  layer?: RuleLayer;
  sensitivity?: 'public' | 'private' | 'unknown';
  sources: ApprovalSource[];
  moreSources: number;
  /** Where the call sends its content, as a person names it (`#sales`, an address). */
  destination?: string;
  /** "Allow for this conversation" may be offered. */
  conversationGrant: boolean;
  /** "Don't ask again" may save the answer as the person's own rule for the tool (BUGS-B). */
  ruleGrant?: true;
  /** AP-06: the person the run acts for may edit the arguments before approving. */
  editable?: true;
  /** With `editable` (WP-56): the tool's argument names in its schema's order, for the edit card. */
  fieldOrder?: string[];
  /** The fields an edition's rule adds to the reason, as the server sent them (`approvalAskRules` reads them). */
  edition?: Readonly<Record<string, unknown>>;
}

/** A tool call held for the user's approval (durable runs, AG-UI `approval_requested`). */
export interface SSEApprovalRequestedEvent {
  interactionId: string;
  toolCallId: string;
  toolName: string;
  displayName?: string;
  /** The call's arguments as JSON text, exactly as journaled; shown as plain text. */
  args: string;
  argsSummary: string | null;
  reason?: ApprovalReason;
  /**
   * `unknown_effect` (UX3): a call that may or may not have happened, waiting for a person to say which. `connect`
   * (G-12): a call waiting for its run's owner to connect the item it needs; it names that item in `connect`.
   */
  kind?: 'approval' | 'unknown_effect' | 'connect';
  /** `connect` cards only: the item the call needs, and where it is connected (G-12). */
  connect?: ConnectTarget;
  /** What the automatic read of the provider answered before the effect was parked (P2.9). */
  reconcile?: { verdict: string; reason: string; lookups: number };
  /** A sub-agent's interaction, shown in the main thread (UX3): its run, and its role. */
  childRunId?: string;
  childRole?: string;
  /** The coding engine that asked (`codex`, `claude_code`, ...), named on the card (UXINT-3). */
  childEngine?: string;
  /** Where it was asked in its run's journal, and after how many model turns: its order and its batch (UXINT-1). */
  seq?: number;
  afterTurns?: number;
  /** How many of its run's asks were decided before it was asked: asks that waited together share it (WP-113). */
  decidedBefore?: number;
  /** The plan step in progress when it was asked. */
  stepId?: string;
  /** What the call would change and cost (C3.71), as the worker read it; checked by `approvalEffectOf`. */
  effect?: ApprovalEffect;
  /** When it was asked (ISO): the steps' header says since when it waits (WP-97). */
  requestedAt?: string;
}

/**
 * What a connect card waits on (G-12): the toolset or MCP server in words, and where it is connected. `instanceId`
 * comes with the owner's live card and timeline only (a saved reply never has it): the sign-in it opens.
 */
export interface ConnectTarget {
  item: string;
  itemKind: 'toolset' | 'mcp';
  connectPath: string;
  instanceId?: string;
  /** WP-64: it was connected, and its vendor rejected the saved sign-in during the task; the person reconnects it. */
  reason?: 'sign_in_rejected';
  /** When the sign-in was rejected (ISO), with `reason`. */
  since?: string;
}

/** One check of a run's verification (G-05): its kind, what it checked, and why it failed, in the gate's words. */
export interface VerificationCheck {
  kind: string;
  passed: boolean;
  target?: string;
  reason?: string;
}

/** A tool the agent found but cannot call: `not_authenticated` is one the person can connect. */
export interface UnavailableTool {
  tool: string;
  toolset: string;
  reason: string;
}

/** A held call's effect preview (UX-03): a bounded unified diff, and a cost the tool itself declares. */
export interface ApprovalEffect {
  diff?: string;
  /** The diff was cut at the worker's bound; the card says so. */
  diffTruncated?: true;
  costEstimate?: { usd: number; basis: 'declared' };
}

/** AG-UI `interaction_waiting` (UXINT-5): how many approvals wait, and whether the run is paused on them. */
export interface SSEInteractionWaitingEvent {
  count: number;
  paused: boolean;
}

/** `answered` (UX3): the person says an action whose outcome was unknown did happen. */
export type ApprovalDecision = Exclude<InteractionDecision, 'expired' | 'cancelled'>;
/**
 * `conversation`: the same tool and destination are allowed for the rest of the conversation. `tool`: main's
 * "Don't ask again", the answer saved as the person's own rule for the tool (BUGS-B).
 */
export type ApprovalScope = 'once' | 'conversation' | 'tool';

/** AG-UI `approval_resolved`: `expired`/`cancelled` close the card without a person's decision. */
export interface SSEApprovalResolvedEvent {
  interactionId: string;
  decision: InteractionDecision;
  childRunId?: string;
  /** Allowed for the rest of the conversation: by the person, or by a grant they gave that covers it (FLOWGRANT). */
  scope?: 'conversation';
}

export type ApprovalPartStatus = 'pending' | 'approved' | 'denied' | 'closed' | 'expired';

export interface PendingApproval extends SSEApprovalRequestedEvent {
  /** `expired`: the run ended before anyone decided. */
  status: 'pending' | 'sending' | 'approved' | 'denied' | 'answered' | 'closed' | 'expired';
  /** The server's refusal of the user's decision (machine code), kept until they try again. */
  errorCode?: string | null;
  /** Approved for the rest of the conversation (UX1). */
  scope?: 'conversation';
  /** The reason the person gave with their denial here (AP-05), for the receipt. */
  denyReason?: string;
  /** AP-06: the arguments the person approved instead of the call's own (JSON text), for the receipt. */
  editedArgs?: string;
  /** The root run the card was asked in: interaction ids repeat across a conversation's runs (UXINT-0). */
  runId?: string;
  /** An ask a rule says needs several approvals, some given, waiting for the rest (counts only). */
  approvals?: { counted: number; required: number };
  /** The viewer's own approval of this ask is counted; someone else's is what it waits for. */
  approvedByYou?: true;
}

/** A message sent to a running task, waiting for the run to read it (`input.consumed` names it by `messageSeq`). */
export interface QueuedSteer {
  messageSeq: number;
  text: string;
}

/** User's collected answers per question (keyed by question uuid in slot state). */
export interface AskUserQuestionAnswer {
  questionUuid: string;
  selectedOptionIds: string[];
  /** Free-text values keyed by option id when isUserInput is true. */
  userInputs: Record<string, string>;
}

export interface PendingAskUserQuestion {
  /** Matches ThreadMessageLike.id for the assistant row (same as MessagePair.key). */
  assistantMessageId: string;
  payload: AskUserQuestionPayload;
  answers: Record<string, AskUserQuestionAnswer>;
  /** Answers the model already received. A resume only carries what is not here or differs. */
  sentAnswers?: Record<string, AskUserQuestionAnswer>;
  /** `skipped`: the person sent a new message instead of answering (UXINT-5 D6). */
  status: 'pending' | 'submitted' | 'persisted' | 'skipped';
}

/** New answers to a reply's question card, sent with its regeneration. */
export interface RegenAnswers {
  messageId: string;
  payload: AskUserQuestionPayload;
  /** `User selections: ...`, as a tapped answer is sent. */
  message: string;
  answers: Record<string, AskUserQuestionAnswer>;
}

/** Artifact produced by a sandbox tool (coding/database). */
export interface SSEArtifactEvent {
  artifactId?: string;
  fileName: string;
  mimeType: string;
  sizeBytes?: number;
  downloadUrl: string;
  artifactType?: string;
  isTemporary?: boolean;
  recordId?: string;
  /** Version number in the artifact registry (1-based, increments on each update). */
  version?: number;
  /** Id of the CODE artifact this was generated from, if any — lets the UI offer "view source". */
  derivedFromCodeArtifactId?: string;
  /**
   * Visibility of the artifact set by the backend.
   * 'VISIBLE' (default) — shown to the user.
   * 'STAGING' — internal intermediate artifact; Python already suppresses these
   * before emitting SSE events, so this field serves as a defensive signal only.
   */
  visibility?: "VISIBLE" | "STAGING";
  /** Set, with nothing else, when the viewer of a shared conversation may not open this file. */
  unavailable?: boolean;
  /**
   * A card presented again replaces the one with the same id; `discarded`
   * removes it. Absent on cards that are never drafts.
   */
  lifecycle?: ArtifactLifecycle;
}

export type ArtifactLifecycle = 'draft' | 'final' | 'discarded';

/**
 * AG-UI `CUSTOM engine_progress` of an engine run: one progress line, artifact
 * change, workspace revision or file diff, keyed by task (tagged `code_task`).
 */
export interface SSEEngineProgressEvent {
  tool: string;
  taskId: string;
  kind: string;
  text: string;
  data?: Record<string, unknown>;
}

/** One step of a durable run's plan (UXR-B1), whichever executor stated it. */
export interface SSEPlanStep {
  taskId: string;
  title: string;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
  deps: string[];
  /** The step names a tool the run's rules ask about; absent: not known. */
  asksFirst?: boolean;
}

/**
 * AG-UI `CUSTOM plan`: the run's whole plan after a change (a snapshot, so
 * the latest replaces the last). A run that never planned sends none.
 */
export interface SSEPlanEvent {
  runId: string;
  steps: SSEPlanStep[];
  currentTaskId: string | null;
}

export type CodeTaskFileChange = 'added' | 'modified' | 'deleted';

export interface CodeTaskFile {
  path: string;
  change: CodeTaskFileChange;
  sizeBytes?: number;
  binary?: boolean;
  tooLarge?: boolean;
  /** A unified diff, as text; never rendered as markup. */
  diff?: string;
  diffTruncated?: boolean;
}

/** A run's spend from the budget ledger (P6.5); null dimensions are unbounded. */
export interface RunBudgetView {
  cap: { usdMicros: number | null; tokens: number | null };
  committed: { usdMicros: number; tokens: number };
  reserved: { usdMicros: number; tokens: number };
  available: { usdMicros: number | null; tokens: number | null };
  exhausted: boolean;
  /** Model calls (children's included) charged an estimate the provider's usage has not replaced (WP-66); absent from an older server. */
  estimated?: number;
}

export type MergeConflictKind = 'both_changed' | 'target_deleted' | 'fork_deleted';

/** What a child's workspace merge into its parent's did (P6.4). */
export interface ChildMergeReport {
  status: string;
  into: string;
  revision: number | null;
  forkRevision: number;
  merged: string[];
  conflicts: Array<{ path: string; kind: MergeConflictKind }>;
  repositoryConflicts: string[];
}

/** The model a run called, as its journal says (MA1); absent from an older server. */
export interface RunModelRef {
  provider: string;
  model: string;
}

export interface RunChildView {
  runId: string;
  role: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  errorType?: string;
  taskId?: string | null;
  merge: ChildMergeReport | null;
  /** `role` names the sub-agent role that set the model; without it the child inherited its parent's. */
  model?: RunModelRef & { role?: string };
}

/** `GET …/runs/:runId/cost` (C10.05): a run's status and spend so far, from the budget ledger. */
export interface RunCostView {
  status: string;
  budget: RunBudgetView | null;
}

/** `GET …/runs/:runId/timeline`: one durable run as its session's owner reads it (U3). */
export interface RunTimelineView {
  runId: string;
  parentRunId: string | null;
  status: string;
  parts: MessagePart[];
  engine: { taskId: string; frames: SSEEngineProgressEvent[]; truncated: boolean } | null;
  approvals: Array<SSEApprovalRequestedEvent & { status: ApprovalPartStatus }>;
  children: RunChildView[];
  /** A child's own merge into its parent's workspace; null for a root run or one never merged. */
  merge: ChildMergeReport | null;
  budget: RunBudgetView | null;
  /** Suggested next questions (UX5), offered after an answered run; absent from an older server. */
  followups?: ActivityFollowup[];
  /** The run's plan (UXR-B1); a `tool_call` part names its step as `stepId`. Absent from an older server. */
  tasks?: SSEPlanStep[];
  model?: RunModelRef;
  /** From the run's pinned spec (QAFIX-7): `loop` or the engine's kind, its effort (null: the model's default) and
   *  how many tools it grants. Absent from an older server or before the spec is pinned. */
  executor?: string;
  reasoningEffort?: string | null;
  toolsGranted?: number;
  /** The coding engine a loop run's `code_task` hands work to (CMDUI); null: none. Absent from an older server. */
  codingEngine?: string | null;
  /** What a `/compact` request did at the start of this run (SLASH2 C2); absent: none was asked. */
  compacted?: ContextCompactedView;
  /** The end-of-run summary (G-07), once the run ended; absent while it runs and from an older server. */
  digest?: RunDigestView;
  /** Calls that waited for the owner to connect an item (G-12): the connect cards a reload seeds. */
  connects?: RunConnectView[];
  /** The run's reach outside its sandbox (WP-63), in journal order; absent from an older server or when none. */
  sites?: SiteActivityView[];
  /** Clones refused because the org does not list the repository's host or owner (WP-63), in journal order. */
  repositoriesUnavailable?: RepositoryUnavailableView[];
  /** What a gateway hid from the run's model calls (WP103-FU-1); absent from an older server or when none. */
  secretsHidden?: SecretsHiddenView;
  /** BGUI-12: the sources its answer cites (`[chunkIndex]` in its final text); absent until it answered and from an
   *  older server. */
  answer?: { citations: RunAnswerCitation[] };
}

/** A cited source of a run's answer, as the chat's live citations carry it; a withheld one keeps only its number. */
export type RunAnswerCitation = StreamingCitationData | { chunkIndex: number; unavailable: true };

/** The credential shapes hidden from a run's model calls, by kind (`openai_key`, …): never a value. */
export interface SecretsHiddenView {
  kinds: string[];
  count: number;
}

/** `x.pipeshub.repository.unavailable` as the timeline projects it; `path` starts with `/`. */
export interface RepositoryUnavailableView {
  host: string;
  path: string;
  reason: 'host_not_allowed' | string;
}

/**
 * One outside-access line (WP-63, Node's `domain/site-activity.ts`): an allowed website request, a refused one, a
 * clone, a held upload. `label` is the server's plain sentence (no id, no query); `reason` is kept for its tone.
 */
export interface SiteActivityView {
  /** What the line says, worded per locale; absent from an older server (then `label` is shown). */
  kind?:
    | 'read'
    | 'write'
    | 'host_level'
    | 'blocked'
    | 'held'
    | 'upload_allowed'
    | 'upload_denied'
    | 'cloned'
    | 'clone_denied'
    | 'push_denied'
    | (string & {});
  host: string;
  path?: string;
  access?: 'read' | 'read-write' | 'host-level';
  use?: 'read' | 'write';
  outcome: 'allowed' | 'blocked';
  /** The egress proxy's reason of a block (`site_read_only`, `site_upload_held`, `host_not_allowed`, …). */
  reason?: string;
  /** A clone's size in bytes (`cloned`). */
  bytes?: number;
  /** The server's English line: shown for a `kind` this build does not word. */
  label: string;
}

/** A call waiting for its run's owner to connect a toolset or MCP server (G-12). */
export interface RunConnectView {
  interactionId: string;
  toolCallId: string;
  toolName?: string;
  displayName?: string;
  item: string;
  itemKind: 'toolset' | 'mcp';
  instanceId?: string;
  connectPath: string;
  reason?: 'sign_in_rejected';
  askedAt?: string;
  status: RunConnectStatus;
  stepId?: string;
  seq?: number;
  afterTurns?: number;
}

export type RunConnectStatus = 'pending' | 'connected' | 'declined' | 'expired' | 'closed';

/** What a terminated run did (G-07), folded by the server from its journal: counts and file names only. */
export interface RunDigestView {
  outcome: 'answered' | 'needs_input' | 'failed' | 'cancelled';
  cutShortBy?: CutShortReason;
  actions: { total: number; failed: number; refused: number; tools: Array<{ name: string; displayName?: string; count: number }> };
  commands: { total: number; failed: number };
  records: { read: number; changed: number };
  /** BGUI-11: with what its file card shows; absent from an older server. */
  artifacts: Array<{ name: string; recordId?: string; mimeType?: string; sizeBytes?: number; artifactType?: string; version?: number }>;
  /** `working`: the changed files it did not deliver (`lib/harness-jobs/job-files.ts`); absent from an older server. */
  files: { added: number; modified: number; deleted: number; working?: number };
  approvals: { asked: number; approved: number; denied: number; unanswered: number };
  questions: { asked: number; open: boolean };
  delegated: number;
  cost: { usdMicros: number; tokens: number } | null;
  /** Part of `cost` is an estimate for a call cut short before the provider reported its usage (WP-66); absent from an older server. */
  costEstimated?: boolean;
  durationMs: number | null;
}

/** `x.pipeshub.context.compacted` as the run's timeline shows it; token counts are estimates, null when unknown. */
export interface ContextCompactedView {
  /** `loop`, or the coding engine's kind. */
  executor: string;
  outcome: 'compacted' | 'nothing_to_compact' | 'unsupported' | 'failed';
  beforeTokens: number | null;
  afterTokens: number | null;
  userMessagesKept: boolean;
  focus?: string;
}

export interface ActivityFollowup {
  text: string;
}

/** `GET …/runs/:runId/resolved` (CPD-5): the run's pin as admitted. */
export interface ResolvedRunView {
  resolved: ResolvedRunConfig;
}

/** `GET …/usage` (SLASH2 C1): the caller's own replies in a conversation, from the budget ledger. */
export interface ConversationUsage {
  runs: number;
  committed: { usdMicros: number; tokens: number };
}

export type ActivityRunStatus = 'queued' | 'running' | 'waiting' | 'completed' | 'failed' | 'cancelled';
export type ActivityCommandStatus = 'running' | 'completed' | 'failed' | 'timed_out' | 'cancelled';
export type ActivityStream = 'stdout' | 'stderr';

/** One command the run executed (UX5 §3.4); `stdout`/`stderr` are the newest output, as text. */
export interface ActivityCommand {
  commandId: string;
  source: 'run_code' | 'engine';
  command: string;
  language?: string;
  status: ActivityCommandStatus;
  exitCode: number | null;
  startedAt: number | null;
  durationMs: number | null;
  stdout: string;
  stderr: string;
  truncated: boolean;
  /** Bytes of output that were not shown live (the full log still has them). */
  liveDropped: number;
  hasLog: boolean;
  /** The skills the command read (FIX-SKILLSHOW), by folder name: the step reads "Read the PDF skill". */
  skills?: string[];
  /** R32ENGFU-FU-1: its engine can lose a command's output, so an empty output reads "No output was captured." */
  outputMayBeLost?: true;
}

/** The actions a step's row names (`toolNames.<action>` in i18n, `step_display.py`'s `StepAction`). */
export type ToolStepAction = 'readPage' | 'webSearch' | 'findFiles' | 'searchFiles' | 'readFile' | 'listFiles';

/** A tool step's typed facts, built by the worker (`app/harness/step_display.py`). */
export interface ToolStepFacts {
  action: ToolStepAction;
  /** A page's host, a search's query, a find's pattern, a file's path. */
  target?: string;
  /** The page's link (http or https only). */
  href?: string;
  /** The page's title, once it was read. */
  title?: string;
  /** A search's hits, or the files a find or a search listed. */
  count?: number;
  /** The links the result offered, without their text fragments: the answer's citations of them point here. */
  sources?: string[];
}

export interface ActivityTool {
  toolCallId: string;
  name: string;
  displayName?: string;
  /** The skills an engine's own call read (FIX-SKILLSHOW): its skill tool, or a file read on a SKILL.md. */
  skills?: string[];
  status: 'running' | 'completed' | 'failed' | 'awaiting_approval' | 'denied' | 'blocked';
  reason?: ApprovalReason;
  refusal?: { rule?: string; code?: string };
  /** A dry run's call (G-03): recorded as what the run would have done, never made; whether it would have asked. */
  dryRun?: { wouldAsk: boolean };
  /** A failed call (ERR-UX): its code, and for the run's owner the redacted detail the model read. */
  failure?: { code: string; detail?: string };
  argsSummary?: string;
  /** Which page, query, pattern or file, and what came of it (WP-115): the chat step's facts, read the same way. */
  step?: ToolStepFacts;
  durationMs?: number;
  /** A finished call's outcome in a line, from its result (ACTRESULTS). */
  outcome?: ActivityStepOutcome;
  /** The fingerprint of the step's detail, read from `…/steps/:toolCallId` when the step is opened. */
  detail?: string;
  /**
   * The detail itself, when the step came with it (a child run's timeline part) or once it was read: what the
   * chat's own step shows of the call, with the same bounds.
   */
  args?: string;
  resultPreview?: string;
  resultSummary?: string;
  /** Unchecked here, as on a part: see `toolResultView`. */
  resultView?: unknown;
  artifactId?: string;
  approvalAnswer?: 'allowed' | 'declined' | 'expired';
  /** The child run the call handed work to (`code_task`, a sub-agent). */
  child?: ActivityChild;
}

export type ActivityStepOutcome =
  | { kind: 'summary'; text: string }
  | { kind: 'found'; count: number }
  | { kind: 'empty' };

/** `GET …/runs/:runId/steps/:toolCallId`'s `step` (ACTRESULTS). */
export type ActivityStepDetail = Pick<
  ActivityTool,
  'toolCallId' | 'args' | 'argsSummary' | 'resultPreview' | 'resultSummary' | 'resultView' | 'artifactId'
> & { fingerprint: string };

export interface ActivityChild {
  runId: string;
  role: string;
  status: 'running' | 'completed' | 'failed' | 'cancelled';
  codeTaskGoal?: string;
  /** A loop sub-agent's goal (CJFIX-B). */
  goal?: string;
}

export interface ActivityTask {
  taskId: string;
  title: string;
  status: 'pending' | 'in_progress' | 'completed' | 'cancelled';
}

export interface ActivityFile {
  path: string;
  change: CodeTaskFileChange;
  diff?: string;
  diffTruncated?: boolean;
  /** Published as one of the run's artifacts (BGUI-11, the server's one classification); absent: a working file. */
  delivered?: true;
}

/** `GET …/runs/:runId/activity`'s `snapshot`: what one run did in its sandbox, as its owner sees it (UX5). */
export interface ActivityView {
  runId: string;
  status: ActivityRunStatus;
  engine: boolean;
  commands: ActivityCommand[];
  omittedCommands: number;
  tools: ActivityTool[];
  tasks: ActivityTask[];
  files: ActivityFile[];
  followups: ActivityFollowup[];
  model?: RunModelRef;
  /** The /command the run started from (SLASH1). */
  slashCommand?: { name: string; version: string; expandedChars: number; owner: string };
  /** A person's `/run` (SLASH3): they ran it, not the agent, and no model was asked. */
  personRan?: true;
}

/** The activity stream's events, by their SSE `event:` name. */
export type ActivityEvent =
  | { type: 'snapshot'; view: ActivityView }
  | { type: 'command'; command: ActivityCommand }
  /** `truncated`: output joined while waiting to be folded lost its oldest part to the terminal's bound. */
  | { type: 'output'; commandId: string; stream: ActivityStream; text?: string; dropped?: number; truncated?: boolean }
  | { type: 'end'; status: string };

export interface WorkspaceVersionView {
  revision: number;
  parentRevision: number | null;
  reason: string;
  createdAt: number;
  fileCount: number;
  contentHash: string;
  restoredByUser: boolean;
  /** The run that wrote it (a turn, a `/run`); absent for a person's restore or upload, or a fork. */
  runId?: string;  /** An `upload` revision's added or replaced paths: what its row lists after a reload. */
  paths?: string[];
}

export interface WorkspaceVersions {
  workspaceId: string;
  head: number;
  versions: WorkspaceVersionView[];
}

/** A session workspace's changes (SLASH2 `/diff`): `from` is the revision before them, `to` the head. */
export interface WorkspaceDiff {
  workspaceId: string;
  head: number;
  from: number;
  to: number;
  files: Array<CodeTaskFile & { sizeBytes: number; additions: number; deletions: number }>;
  additions: number;
  deletions: number;
  /** Changed paths past the listed ones. */
  omitted: number;
}

/** A workspace revision's files under a folder (SLASH3 `/files`): sizes only, never content. */
export interface WorkspaceFileList {
  workspaceId: string;
  revision: number;
  /** When the revision was saved (ms). */
  createdAt: number;
  files: Array<{ path: string; sizeBytes: number; mode?: number; hidden?: boolean }>;
  totalBytes: number;
  omitted: number;
  /** Fetched copies (ADR-RUN-01 D2): recorded by address and version, never saved, so never among `files`. */
  references?: WorkspaceReference[];
}

/** A repository the engine fetched, as `/files` names it: "onyx-dot-app/onyx at 57cb7b8 — fetched copy, not saved". */
export interface WorkspaceReference {
  path: string;
  label: string;
  address: string;
  version?: string;
}

/** `/run` (SLASH3 §3.5): the line, and the chat's mode, model and tools as its next message would send them. */
export interface SandboxCommandRequest {
  command: string;
  timeoutS?: number;
  idempotencyKey: string;
  chatMode: string;
  modelKey?: string;
  modelName?: string;
  modelProvider?: string;
  modelFriendlyName?: string;
  tools?: string[];
  agentCapabilities?: { internalSearch: boolean; webSearch: boolean; deepSearch?: boolean };
}

/** What `/sandbox` shows (SLASH3 §3.6): composed by the server without starting a sandbox. */
export interface SessionSandboxView {
  image: { name: string; isolation: string | null };
  network: boolean;
  /** How a code run is decided before it starts. */
  codeRuns: 'allow' | 'ask' | 'deny';
  workspace: { revision: number; files: number; bytes: number };
  keptSignIn: { idleMinutes: number; maxHours: number; signedInAt?: string } | null;
  /** This chat's own coding engine from its next message (`/engine`): a kind, `none`, or null (not set). */
  codingEngine: string | null;
}

/** One workspace file as `/open` read it: at most the bytes asked for, with the full size. */
export interface WorkspaceFileRead {
  blob: Blob;
  contentType: string;
  sizeBytes: number;
  truncated: boolean;
  revision: number;
}

/** What the viewer may decide on a run's approvals (the decision route's rule). */
export interface DecisionAuthority {
  approve: boolean;
  deny: boolean;
  /** AP-06: the viewer is the person the run acts for, so may edit a call before approving it. */
  edit?: boolean;
  /** What rules beyond the usual one add for the run's asks they decide (`ApprovalAskRules`). */
  [field: string]: unknown;
}

/** Artifact metadata attached to a chat slot for display. */
export interface ChatArtifact {
  id: string;
  fileName: string;
  mimeType: string;
  sizeBytes: number;
  downloadUrl: string;
  artifactType: string;
  recordId?: string;
  /** Version number in the artifact registry (1-based, increments on each update). */
  version?: number;
  /** Id of the CODE artifact this was generated from, if any — lets the UI offer "view source". */
  derivedFromCodeArtifactId?: string;
  /** Mirrors the backend visibility field — STAGING artifacts should never be rendered. */
  visibility?: "VISIBLE" | "STAGING";
  /** A file in a conversation shared with you that you may not open; nothing else about it is known. */
  unavailable?: boolean;
  /** A draft is shown as such until its final card replaces it. */
  lifecycle?: Exclude<ArtifactLifecycle, 'discarded'>;
}

export interface SSEConnectedEvent {
  message: string;
  /** Set by Node when a new conversation row is created before streaming (main + agent). */
  conversationId?: string;
  /**
   * Initial title persisted with that row (same source as list/detail APIs).
   * Present on `connected` for new streams only — avoids a separate GET for sidebar UX.
   * If the backend later async-refines titles, `complete` still refreshes list/title state.
   */
  title?: string;
}

/** Backend status phases (planning / tools / generation); keep open-ended for forward compatibility */
export interface SSEStatusEvent {
  status: string;
  message: string;
}

export interface ChatCitation {
  id: string;
  title: string;
  url?: string;
  type: string;
}

/**
 * Citation structure from API response (GET /api/v1/conversations/:id)
 * More detailed than ChatCitation - this is the raw backend format.
 * Transformed to ChatSource for display via loadHistoricalMessages().
 */
export interface CitationApiResponse {
  citationId: string;
  citationData: {
    _id: string;
    content: string;
    chunkIndex: number;
    /** A source the shared viewer cannot read; it carries no content or metadata. */
    unavailable?: boolean;
    metadata: {
      recordName: string;
      recordId: string;
      connector: string;
      /** Connector instance the record came from; absent on older saved answers */
      connectorId?: string;
      connectorInstanceName?: string;
      recordType: string;
      webUrl?: string;
      mimeType: string;
      pageNum?: number[];
      blockNum?: number[];
      extension: string;
      previewRenderable: boolean;
      orgId: string;
      recordVersion: number;
      topics?: string[];
      languages?: string[];
      departments?: string[];
      /** When the cited record was last updated at its source */
      updatedAt?: string;
    };
    citationType: string;
    createdAt: string;
    updatedAt: string;
  };
}

export interface SSEAnswerChunkEvent {
  chunk: string;
  accumulated: string;
  citations: SSEChunkCitation[];
  confidence?: string;
}

/**
 * Raw citation shape received in SSE answer_chunk events.
 * Unlike CitationApiResponse, these do NOT have citationId — only chunkIndex.
 */
export interface SSEChunkCitation {
  content: string;
  chunkIndex: number;
  metadata: {
    orgId: string;
    recordId: string;
    virtualRecordId?: string;
    recordName: string;
    recordType: string;
    recordVersion: number;
    origin: CitationOrigin;
    connector: string;
    blockText: string;
    blockType: string;
    bounding_box?: Array<{ x: number; y: number }>;
    pageNum?: number[];
    extension: string;
    mimeType: string;
    blockNum?: number[];
    webUrl?: string;
    previewRenderable: boolean;
    hideWeburl: boolean;
  };
  citationType: string;
}

export interface ReferenceData {
  id: string;
  title: string;
  summary?: string;
  category?: string;
  topics?: string[];
  url?: string;
  type: string;
}

export interface ToolCallEntry {
  toolName: string;
  toolResult: Record<string, unknown>;
}

/** One model-turn's persisted chain-of-thought (opt-in server-side; see reasoning_persistence.py). */
export interface ReasoningTurn {
  messageId?: string;
  turnIndex?: number;
  content: string;
}

/**
 * One entry in the ordered agent-activity transcript — text, reasoning, a
 * tool call, or a nested sub-agent's own timeline. Mirrors the Python
 * `TranscriptCollector`'s `MessagePart` / Node's `IMessagePart` byte-for-
 * byte; built live from AG-UI wire events during streaming
 * (`agui-event-handler.ts`) and rendered identically from persisted
 * `ConversationMessage.parts` after reload — see the "Parts-Based Agent
 * Message Transcript" plan.
 *
 * `resultPreview` is always a bounded preview (~500 chars), never the full
 * external tool result — the server never sends more than that.
 */
/** On an `mcp_sign_in` part: an MCP server whose sign-in lacked permission this turn (Python `mcp_sign_in.py`). */
export interface McpSignInServer {
  instanceId: string;
  serverName: string;
  scopes: string[];
  /** Set for a service-account agent's own sign-in, redone through the agent's route. */
  agentKey?: string;
}

/** DEEPROUTE: a run handing its work to the coding engine on its own (`x.pipeshub.run.handoff`). */
export interface CodeTaskHandoff {
  /** `upfront` (before its first step) or `escalation` (once it outgrew a reply). */
  trigger: string;
  /** Why: `research`, `analysis`, `planning`, `long_report`, `many_steps`, `context` or `budget`. */
  reason: string;
  /** The engine kind; absent from a server that did not name it. */
  engine?: string;
}

export interface MessagePart {
  /** `user_input`: a message the user sent while the run worked, where the run took it. */
  /**
   * `engine_task`: a durable engine run's progress lines (`frames`), the card a
   * reload rebuilds; `approval`: a call held for a person's approval (U3);
   * `plan`: a durable run's plan as it stood at the end (`steps`, CTL-3b); `notice`:
   * something the run tells the person beside its steps (`code`, CTL-5b); `engine_command`: one command of an
   * engine run as its timeline shows it, made in the chat from the `engine_task` frames, never saved (CTL-4);
   * `engine_file`: one file it created, changed or deleted, made the same way (`file`).
   */
  type: "text" | "reasoning" | "tool_call" | "sub_agent" | "user_input" | "engine_task" | "approval" | "plan" | "notice" | "engine_command" | "engine_file" | "slash_command" | "mcp_sign_in" | "context_compacted" | "engine_repo";
  /** `engine_repo`: an engine run's repository work in plain words (WP-26), made like `engine_command`. */
  repo?: RepoActivity;
  /** `engine_file`: the file and its diff, when the run saved one. */
  file?: CodeTaskFile;
  /** `slash_command`: the /command the run started from (SLASH1), the timeline's first row. */
  commandName?: string;
  /** `context_compacted`: what the person's `/compact` did before the run's first turn (SLASH2 C2), saved with the answer. */
  compacted?: ContextCompactedView;
  commandVersion?: string;
  expandedChars?: number;
  owner?: string;
  /** `engine_command`: the command line and its exit code; `content` holds what it printed. */
  command?: string;
  /** `engine_command` and an engine's `tool_call`: the skills the step read (FIX-SKILLSHOW), named by the server. */
  skills?: string[];
  exitCode?: number;
  /** `notice`: what it says, as the chat words it (e.g. `mcp_unavailable`). */
  code?: string;
  /** `notice` `credential_missing`: the toolset or MCP server the person has not connected (QA23-F1). */
  item?: string;
  /** `notice` `engine_switched` (WP-111): the coding engine used instead of `item`, which an admin turned off. */
  engine?: string;
  /** `notice` `mcp_unavailable`: the server instance to sign in to again (`sign_in`) or for an admin to approve (`not_approved`). */
  instance?: string;
  /** `notice` `mcp_tool_held` (D-MCP-5): the tools of the server in `item` the description scanner held back. */
  tools?: string[];
  /** `notice`: the item's kind; `approval`: `connect` for a call that waited for the owner to connect an item (G-12). */
  kind?: 'toolset' | 'mcp' | 'connect';
  /** `approval` of kind `connect`: what it waited on (never the instance). */
  connect?: ConnectTarget;
  /** `notice` `verified` / `unverified` (G-05): every check of the run's last verification. */
  checks?: VerificationCheck[];
  /** `notice` `verified` / `unverified`: the round that settled it (2: the run was sent back once to fix it). */
  round?: number;
  /** `notice` `time_plan` (ADR-RUN-01 D4): when the run starts writing at the latest, and when its work is due. */
  writeBy?: string;
  finishBy?: string;
  content?: string;
  /** `user_input`: the message's number in its run. */
  messageSeq?: number;
  /** `user_input`: `false` when the run ended without taking it. */
  consumed?: boolean;
  /** `user_input`: the files the message carried (C-21). */
  attachments?: AttachmentRef[];
  toolCallId?: string;
  toolName?: string;
  displayName?: string;
  args?: string;
  /** Human-readable summary of `args`, computed server-side. Falls back to `args` when absent. */
  argsSummary?: string;
  /** `approval` parts: `pending`, `approved`, `denied`, `closed` or `expired`. */
  status?: "running" | "completed" | "failed" | "blocked" | "awaiting_approval" | ApprovalPartStatus;
  resultPreview?: string;
  /** A failed call's `ToolErrorCode` (ERR-UX): the card words it; the detail never reaches the part. */
  errorCode?: string;
  /** A failed write or execute whose outcome is definite: nothing was sent. */
  nothingSent?: boolean;
  /** Human-readable summary of the tool result, computed server-side from the full (untruncated) output. Falls back to `resultPreview` when absent. */
  resultSummary?: string;
  /** A table or field list of the result, built server-side; unchecked here, see `toolResultView`. */
  resultView?: unknown;
  /** What the step did, on what, and what came of it (WP-112): its row says which page, query, pattern or file. */
  step?: ToolStepFacts;
  /** On an `awaiting_approval` call: what the approval card shows. */
  approval?: ToolApprovalDetails;
  /** The call a person approved, run when they answered. */
  approved?: boolean;
  /** How a call that waited for approval was settled (Python `tool_approvals.Outcome.answer`). */
  approvalAnswer?: 'allowed' | 'declined' | 'expired';
  /** On the `mcp_sign_in` part: the reply's sign-in card. Not activity, so never in the timeline. */
  servers?: McpSignInServer[];
  /** On the final `text` part: the limit that cut the run's work short (BUDGETEND). */
  truncatedBy?: CutShortReason;
  /** With `truncatedBy` `budget`: which budget beyond the run's own ran out (C9.06, WP-51). */
  budgetScope?: BudgetScope;
  /** On a `notice` (WP-89): which time a state notice was journaled; each occurrence is its own part. */
  occurrence?: number;
  /** On the final `text` part (WP-89): the run's settled outcome, which the chat words; read by `runOutcomeOf`. */
  runOutcome?: unknown;
  /** On the final `text` part (ANSWER-CONTRACT-FU-2): the subject was not found; read by `notFoundOf`. */
  notFound?: unknown;
  /** On the final `text` part (R32REPO-FU-1): what the run tried and could not do; read by `couldNotDoOf`. */
  limits?: unknown;
  runId?: string;
  roleName?: string;
  parts?: MessagePart[];
  /** `sub_agent` of a durable child: the run that spawned it, and why it did not complete. */
  parentRunId?: string;
  errorType?: string;
  /** `sub_agent` that ran out of memory: its sandbox's memory limit in MB, which its card names (R32SECCOMP-FU-4). */
  memoryLimitMb?: number;
  /** `sub_agent`: the model the child called, its own or the one it inherited (MA1). */
  model?: RunModelRef;
  /** `sub_agent` of a `code_task` handoff: the engine child's goal, its row's name (CTL-6). */
  codeTaskGoal?: string;
  /** `sub_agent` of a loop sub-agent: its `spawn_agent` goal, its job card's name (CJFIX-B), never the role id. */
  goal?: string;
  /** `sub_agent` of a hand-off the run made on its own (DEEPROUTE): why, and to which engine; its row's name. */
  codeTaskHandoff?: CodeTaskHandoff;
  /** `sub_agent` of an engine's own helper (EV-07), made in the chat from its run's frames: what it was asked. */
  engineHelper?: { task?: string };
  /** `approval`: the interaction the decision route takes; an engine's `tool_call`: the approval it waited for. */
  interactionId?: string;
  /** `approval`: where it was asked in the run's journal and after how many model turns (UXINT-1). */
  seq?: number;
  afterTurns?: number;
  /** `approval`: how many of its run's asks were decided before it was asked (WP-113). */
  decidedBefore?: number;
  /** `approval`: when it was asked (ISO, WP-97), unchecked until `savedApprovals`. */
  requestedAt?: unknown;
  /**
   * `approval`: why the run asked (UX1) and whether it was approved for the conversation. A `notice`
   * `mcp_unavailable` carries a string here instead (MCPNOTE-1), read by `mcpUnusedGroups`.
   */
  reason?: ApprovalReason;
  scope?: 'conversation';
  /** `approval`: what the call would change and cost (C3.71), unchecked until `approvalEffectOf`. */
  effect?: unknown;
  /** `approval`: the reason given with its denial (AP-05), quoted by the receipt. */
  denyReason?: unknown;
  /** `approval`: the arguments the person approved instead (AP-06), JSON text; unchecked until `approvalFromPart`. */
  editedArgs?: unknown;
  /** `engine_task`: the task (the engine run's id) and its progress lines, oldest first. */
  taskId?: string;
  frames?: SSEEngineProgressEvent[];
  /** `plan`: the run's steps with their last state (CTL-3b). */
  steps?: SSEPlanStep[];
  /** A saved durable step's time from the journal: a model turn's text part or a tool call (CTL-3b). */
  durationMs?: number;
  /** The final text part of a saved durable answer: the run's whole time (CTL-3b). */
  elapsedMs?: number;
  truncated?: boolean;
  /**
   * Set ONLY on the one root-level `text` part that IS the final answer
   * (mirrors Python's `TranscriptCollector.replace_final_text`) — every
   * other root `text` part is narration (Cursor-Style Agent Transparency
   * plan) and renders in the activity timeline instead. Never set on
   * child (sub-agent) text parts.
   */
  isFinal?: boolean;
  /**
   * Set on a root-level, non-`isFinal` `text` part once a later root event
   * proves it was narration rather than the (still-pending) final answer —
   * a tool call starting, a reasoning block starting, or a new text turn
   * starting all imply "more work follows this turn" (see
   * `agui-event-handler.ts`'s `LivePartsBuilder.settleLastRootText()`,
   * called from each of those three event handlers). The live answer
   * buffer (`streamingContent`) is cleared in the same event, so the text
   * is no longer duplicated there — `filterRootParts` shows `settled` text
   * in the activity timeline as narration even while still streaming, and
   * hides any not-yet-`settled` text (still mirrored in the answer buffer).
   * `TEXT_MESSAGE_END` alone does NOT settle the part — at that point it's
   * still ambiguous whether narration (tool call/reasoning follows) or the
   * final answer (`RUN_FINISHED` follows) is next.
   */
  settled?: boolean;
}

export interface ConversationMessage {
  _id: string;
  messageType: "user_query" | "bot_response" | "tool_call" | "error";
  content: string;
  contentFormat: "MARKDOWN";
  citations: CitationApiResponse[];
  confidence?: "Very High" | "High" | "Medium" | "Low";
  followUpQuestions: string[];
  referenceData: ReferenceData[];
  modelInfo: ModelInfo;
  createdAt: string;
  updatedAt: string;
  feedback: Record<string, unknown>[];
  appliedFilters?: AppliedFilters;
  /** File attachments uploaded with this user query (PDF / JPEG / PNG). */
  attachments?: AttachmentRef[];
  /** `user_query` sent as a /command (SLASH1): what was typed; `content` is the text that was sent. */
  command?: unknown;
  tools?: ToolCallEntry[];
  /** Present only when the org has reasoning persistence enabled server-side. */
  reasoning?: ReasoningTurn[];
  /** Persisted agent-activity transcript (`agui` protocol only) — see MessagePart. */
  parts?: MessagePart[];
  /** Set when this bot response was cut short by a user-initiated Stop (see Node's `IMessage.status`). */
  status?: 'stopped';
  /** In a conversation continued privately: stands for another participant's turn, whose content was not copied. */
  foreignTurn?: boolean;
  /** Copied from another conversation by a fork: its saved steps show, but it names none of that conversation's runs. */
  copied?: boolean;
  /** The durable run that wrote this answer, when the server records it; always a run of this conversation. */
  runId?: string;
  /** An error answer's code (`messageType: 'error'`): shown in the reader's words (QA17-O2). */
  errorCode?: string;
  /** With errorCode `mcp_tool_withdrawn`: the labels of the servers the words name (WP-84). */
  errorServers?: string[];
  /** An answer a limit cut short (BUDGETEND): the spending limit, the turn limit or the working time. */
  truncatedBy?: CutShortReason;
  /** With `truncatedBy` `budget`: which budget beyond the run's own ran out (C9.06, WP-51); absent: the run's own limit. */
  budgetScope?: BudgetScope;
  /** With errorCode `operator_stopped` (ADR-OPS-01 D5): the control's scope and reason, read by `operatorStopDetails`. */
  errorStop?: { scope?: string; reason?: string };
  /** WP-89: the run's settled outcome (a failure's code, servers and stop among it); read by `runOutcomeOf`. */
  runOutcome?: unknown;
  /** ANSWER-CONTRACT-FU-2: the subject was not found; read by `notFoundOf`. */
  notFound?: unknown;
  /** R32REPO-FU-1: what the run tried and could not do; read by `couldNotDoOf`. */
  limits?: unknown;
  /** The server withheld this answer: it used a record the reader can no longer access, or access could not be confirmed. */
  answerHidden?: { redacted: true; reason: 'no_longer_accessible' | 'access_check_unavailable' };
}

/**
 * Which budget beyond the run's own stopped an answer (C9.06), as the extension that keeps such budgets names it;
 * opaque here, and worded only by `budgetStopLines`.
 */
export type BudgetScope = string;

export interface ConversationCompleteData {
  userId: string;
  orgId: string;
  title: string;
  initiator: string;
  messages: ConversationMessage[];
  isShared: boolean;
  isDeleted: boolean;
  isArchived: boolean;
  lastActivityAt: number;
  status: string;
  modelInfo: ModelInfo;
  _id: string;
  sharedWith: string[];
  conversationErrors: string[];
  createdAt: string;
  updatedAt: string;
  __v: number;
  projectId?: string;
  projectVisibility?: 'private' | 'project';
}

export interface SSECompleteEvent {
  conversation: ConversationCompleteData;
  meta: {
    requestId: string;
    timestamp: string;
    duration: number;
    searchDegraded?: boolean;
    accessNotices?: unknown;
    /** Name-only hits for records whose content is not indexed yet. */
    unindexedMatches?: UnindexedMatch[];
  };
}

export interface SSEErrorEvent {
  error: string;
  message: string;
  code?: string;
  /**
   * Stable error classification the backend derives from the underlying
   * failure (`rate_limit` / `request_too_large` / `auth_error` /
   * `invalid_request` / `server_error` / `timeout` / `unknown` — see
   * `error_classification.py`).
   * `message` is already a user-friendly string for any of these, so this
   * is only needed if a caller wants to branch on the failure kind (e.g.
   * offer a "try again" affordance for `rate_limit` specifically, or show
   * a "shorten your message" hint for `request_too_large`).
   */
  type?: string;
}

// Status message for display during streaming
export interface StatusMessage {
  id: string;
  /** Mirrors SSE status when applicable (`connected`, `planning`, `executing`, …) */
  status: string;
  message: string;
  timestamp: string;
}

/** Status of a running tool that reported its own progress (e.g. "waiting in queue"):
 * shown even when the tool's running row would otherwise stand in for the status. */
export const TOOL_PROGRESS_STATUS = 'tool_progress';

// Request payload for streaming API
export interface StreamChatRequest {
  query: string;
  modelKey: string;
  modelName: string;
  modelFriendlyName: string;
  /**
   * `quick` for normal modes; when query mode is Agent, `agent:<segment>`
   * e.g. `agent:auto`, `agent:planExecute` (UI “Plan & Execute” strategy).
   */
  chatMode: StreamChatModePayload;
  filters: ChatKnowledgeFilters;
  timezone?: string;
  currentTime?: string;
  appliedFilters?: AppliedFilters;
  conversationId?: string;
  /** When set, the stream uses /api/v1/agents/:id/conversations/.../stream */
  agentId?: string;
  /**
   * Agent streams only → JSON `tools`: explicit tool `fullName` subset.
   * `undefined` (field omitted) means "every configured toolset, no
   * filter" — NEVER resolved into an exploded full-catalog array on this
   * type, since that would defeat the backend's own "no filter" handling
   * and needlessly approach its request-size cap. `[]` means "none".
   */
  agentStreamTools?: string[];
  /**
   * Agent mode capability overrides. When present and `chatMode` is
   * `'agent'` or `'agent:*'`, the backend gates `has_knowledge` /
   * `web_search_config` accordingly. Omitting this field keeps today's
   * default behavior (both capabilities enabled).
   */
  agentCapabilities?: AgentCapabilities;
  /** Uploaded file refs to include with this message (PDF / JPEG / PNG). */
  attachments?: AttachmentRef[];
  /**
   * Only sent when the user has explicitly picked a non-default effort for a
   * reasoning-capable model. Omitted → backend uses the model's own default.
   */
  reasoningEffort?: ReasoningEffort;
  /**
   * Client-generated UUID for this run, minted before the fetch in
   * `streamMessageForSlot`/`streamRegenerateForSlot`. Lets a later
   * `POST .../cancel` (see `ChatApi.cancelStream`) target this exact run —
   * see `cancelRunBodySchema` (Node) / `CancelRunRequest` (Python).
   */
  runId?: string;
  /**
   * Links a brand-new conversation to a Project. Only meaningful when
   * `conversationId` is absent — once a session exists its `projectId` is
   * the source of truth server-side and this field is ignored on follow-ups
   * (see Node `es_controller.ts` / plan's "projectId on follow-up requests
   * is ignored").
   */
  projectId?: string;
  /**
   * `durable`: the default chat's Agent mode asks for the harness (ASSIST1).
   * `legacy`: the "New agent harness" switch is off (CTL-7). The server
   * decides; see `chatHarnessField`.
   */
  harness?: 'durable' | 'legacy';
  /**
   * The coding engine `/engine` chose before this chat's first message (CMDUI2): only on the request that creates
   * the chat. The server checks it as `/engine` does and saves it on the chat before the run starts.
   */
  codingEngine?: string;
  /**
   * The GitHub repository chosen before this chat's first message (WP-26): only on the request that creates the chat.
   * The server checks it as `PUT …/repository` does and saves it on the chat before the run starts.
   */
  repository?: { url: string; baseRef?: string; toolsetInstanceId: string };
  /** This message's run gates (G-03..G-05) from the composer's "+" menu; only the agent harness serves them. */
  gates?: TurnGatesRequest;
  /** The person's answer to a tool approval card; the call itself is saved server-side. */
  toolApproval?: ToolApprovalAnswer;
  /** Compare mode only: asks the server for a `turn_started` event once the question is saved. */
  compareTurn?: true;
}

export type ToolApprovalDecision = 'allow' | 'deny';

export interface ToolApprovalAnswer {
  approvalId: string;
  decision: ToolApprovalDecision;
  /** "Don't ask again for this tool": the answer becomes the person's own rule for it. */
  remember?: boolean;
}

/** A tool call waiting for a person's approval (Python `tool_approvals._ask`). */
export interface ToolApprovalDetails {
  approvalId: string;
  instanceId: string;
  serverName: string;
  toolName: string;
  toolTitle?: string | null;
  readOnly?: boolean;
  /** What the tool does to data; a deleting tool is marked on the card. */
  kind?: 'read' | 'write' | 'destructive' | null;
  /** False when "Don't ask again" can't set a rule: a toolset's rules are its admin's. */
  canRemember?: boolean;
  /** Epoch milliseconds. */
  expiresAt: number;
  /** Null when the arguments were too large to send; `argumentsPreview` holds their start. */
  arguments?: Record<string, unknown> | null;
  argumentsPreview?: string;
}

/**
 * Builds mode-related fields for stream/regenerate payloads from settings.
 *
 * `isScopedAgent` distinguishes the two very different consumers of Agent
 * `QueryMode`: a conversation scoped to a specific custom Agent (`agentId`
 * set) still needs the `agent:<strategy>` wire value — its endpoint
 * (`agent.py`) unwraps that via {@link streamChatModeToAgentApiChatMode} to
 * drive `PipesHubAgentFactory`'s loop selection. The *main* chat's Agent
 * mode (no `agentId` — "Universal Agent Mode") has no such strategy
 * concept server-side (`chat_modes/policy.py`'s `AGENT_POLICY` decides
 * internal-search vs web-search vs both from the query itself), so it must
 * send the plain `'agent'` value instead.
 */
export function buildStreamRequestModeFields(
  settings: ChatSettings,
  isScopedAgent = false,
): Pick<StreamChatRequest, "chatMode"> {
  if (settings.queryMode === "agent") {
    return isScopedAgent
      ? {
          chatMode: `agent:${agentStrategyToApiSegment(settings.agentStrategy)}`,
        }
      : { chatMode: "agent" };
  }
  if (settings.queryMode === "web-search") {
    return {
      chatMode: "web_search",
    };
  }
  return {
    chatMode: "internal_search",
  };
}

// ── Message Action Types ──

/** Model override for regenerate / edit query flows. */
export interface ModelOverride {
  modelKey: string;
  modelName: string;
  modelFriendlyName: string;
  /** Provider key from GET …/available/llm (e.g. `openAI`). */
  modelProvider?: string;
}

/** A single LLM model entry from GET /api/v1/configurationManager/ai-models/available/llm */
export interface AvailableLlmModel {
  modelType: string;
  provider: string;
  modelName: string;
  modelKey: string;
  isMultimodal: boolean;
  isReasoning: boolean;
  isDefault: boolean;
  modelFriendlyName: string;
  /** The model's own effort, used when the request sets no reasoningEffort and the agent has no default. Absent when nothing valid is stored; a legacy `none` is shown as Low. */
  defaultReasoningEffort?: ReasoningEffort;
}

/**
 * Transient UI state for the active message action indicator.
 * Lives in ChatInput (component-local, NOT in the Zustand store) to avoid
 * unnecessary selector evaluations and rerenders across unrelated components.
 */
export type ActiveMessageAction =
  | null
  | { type: 'regenerate'; messageId: string; appliedFilters?: AppliedFilters }
  | { type: 'editQuery'; messageId: string; text: string; appliedFilters?: AppliedFilters; attachments?: AttachmentRef[] };

// ── Multi-Chat Slot Types ──

/** Maximum number of concurrent chat slots before LRU eviction. */
export const MAX_SLOTS = 15;

/** Public coordinates needed to issue an authenticated durable-run command. */
export interface DurableAgentRunHandle {
  agentId?: string;
  sessionId: string;
  runId: string;
}

/**
 * Per-conversation state stored in the slots dictionary.
 *
 * Each slot represents one open conversation — the single
 * `useExternalStoreRuntime` reads from the active slot.
 */
export interface ChatSlot {
  /** Server-assigned conversation ID, or null for a brand-new chat. */
  convId: string | null;
  /**
   * When set, this thread is scoped to an agent (`/chat?agentId=…`).
   * Used to keep agent conversations out of the main chat sidebar and URL sync.
   */
  threadAgentId: string | null;
  /**
   * Tool fullNames for agent SSE, captured when the thread is scoped to an agent.
   * Lets background / off-URL agent slots stream with the correct tools without
   * relying on the global `agentStreamTools` (which tracks the current URL agent only).
   */
  agentStreamTools: string[] | null;
  /**
   * Project this thread is linked to (`/chat?projectId=…`), or null.
   * Only used to seed `StreamChatRequest.projectId` on the first message of
   * a brand-new chat — once `convId` is assigned, the session row is the
   * source of truth and this field is not sent on follow-ups.
   */
  projectId: string | null;
  /** True until the server assigns a real convId. */
  isTemp: boolean;
  /** True once messages have been loaded (or immediately for new chats). */
  isInitialized: boolean;
  /**
   * Bumped by every `invalidateConversation`, including one that arrives while a history load is
   * in flight; the loader discards and repeats a response fetched under an older value.
   */
  refreshGeneration: number;
  /** True after first successful message load — drives tracker selection. */
  hasLoaded: boolean;
  /**
   * The loaded conversation was still `Inprogress` on the server — a run that kept going after this
   * tab's connection dropped (refresh, closed tab). Drives `startRemoteRunPoll`.
   */
  remoteRunInProgress: boolean;

  /** Messages in assistant-ui ThreadMessageLike[] format. */
  messages: ThreadMessageLike[];

  // ── Per-slot streaming state ──
  isStreaming: boolean;
  streamingContent: string;
  streamingQuestion: string;
  currentStatusMessage: StatusMessage | null;
  streamingCitationMaps: CitationMaps | null;
  /**
   * Live agent-activity transcript for the message currently streaming —
   * built incrementally from AG-UI events (see `agui-event-handler.ts`'s
   * `onParts` callback) so thinking/tool-call/sub-agent activity renders
   * as it happens instead of only appearing after reload. Cleared when a
   * new stream starts; the array reference is replaced (not mutated) on
   * every update so it works as a plain render dependency.
   */
  streamingParts: MessagePart[];

  // ── Per-slot scroll state (persisted across switches) ──
  userScrollOverride: boolean;
  /** Captured on switch-away, restored on switch-in. null = never saved. */
  savedScrollTop: number | null;
  /**
   * True if savedScrollTop was captured while the slot was actively streaming.
   * When true and the slot is no longer streaming on restore, treat the chat
   * as historical (scroll to top-of-last-message) instead of restoring the
   * stale mid-stream position.
   */
  savedScrollWasStreaming: boolean;

  // ── Per-slot UI state ──
  /**
   * The messageId of the message currently showing a non-answer tab
   * (sources or citations). Only one message per slot can be expanded
   * at a time. null means all messages show the Answer tab.
   */
  activeExpandedMessageId: string | null;
  regenerateMessageId: string | null;
  /** Assistant row this run is writing into. A resume can be an older card. */
  streamingAssistantId: string | null;
  pendingCollections: ChatCollectionAttachment[];

  /** Artifacts produced during the current streaming response. */
  artifacts: ChatArtifact[];

  /**
   * Pending interactive questionnaire from SSE `ask_user_question`.
   * Stays on the same assistant row through submit (`status: 'submitted'`)
   * so the follow-up answer streams in-place. Replaced by
   * `persistedAskUserQuestion` after `onComplete` / reload.
   */
  pendingAskUserQuestion: PendingAskUserQuestion | null;

  /** Tool calls of the durable run waiting for the user's approval (P2.6c). */
  pendingApprovals: PendingApproval[];

  /** What the run says waits on a person (UXINT-5): the count a shared viewer sees, and whether it is paused. */
  interactionWaiting?: SSEInteractionWaitingEvent | null;

  /** Messages sent to the running task that it has not read yet, shown until it does (UXINT-5). */
  queuedSteers?: QueuedSteer[];

  /** The latest plan of the run streaming in this slot (UXR-B1); none until it plans. */
  plan?: SSEPlanEvent | null;

  /** AbortController for the in-flight SSE stream (if any). */
  abortController: AbortController | null;

  /** Set only after durable admission; browser detach leaves the run alive. */
  durableRun: DurableAgentRunHandle | null;
  /** The latest durable run of this conversation in this session, kept after it ends (UX5's panel and chips). */
  lastDurableRun?: DurableAgentRunHandle | null;

  /**
   * A turn sent on the chat route whose first frame has not arrived: the
   * server has not yet said whether the durable harness or today's loop
   * answers it, so a Stop waits for that frame to know which run to stop.
   */
  routePending: boolean;

  /**
   * Client-generated UUID for the in-flight run, minted in
   * `streamMessageForSlot`/`streamRegenerateForSlot` and sent to the backend
   * so a later Stop can target this exact run. `null` when nothing is
   * streaming. NOT cleared until the run fully settles (grace-timeout abort
   * fallback in `stopStreamForSlot` still needs it after `stopping` starts).
   */
  runId: string | null;
  /**
   * True from the moment Stop is clicked until the run actually ends
   * (`RUN_FINISHED` with `status: 'stopped'`, or the 5s grace-timeout abort
   * fallback in `stopStreamForSlot`). Disables a second Stop click / shows a
   * "Stopping…" affordance instead of hard-aborting immediately, so Python
   * gets a chance to persist the partial answer before the connection dies.
   */
  stopping: boolean;

  /**
   * Tracks message pagination for the "load older messages" flow.
   * null = not yet set (happens after first successful load).
   * hasOlderMessages mirrors the API `hasNextPage` (next page = older batch).
   */
  messagePagination: {
    currentPage: number;
    hasOlderMessages: boolean;
    isLoadingOlder: boolean;
  } | null;

  /**
   * The stored rows `messages` was built from. "Load older messages" joins an
   * older page onto these and rebuilds, so a card that spans two pages comes
   * out as one card. `null` when the thread was not built from stored rows.
   */
  rawMessages: ConversationMessage[] | null;

  /** Set when this chat is a fork; `null` for any other chat or before the detail has loaded. */
  forkedFrom: ForkOrigin | null;
  forks: ForkChild[];

  /** Epoch ms of last interaction — used for LRU eviction. */
  lastAccessedAt: number;

  /**
   * From GET conversation detail `access.isOwner`.
   * `null` until the first fetch completes; `false` for chats opened from Shared Chats (hide share UI).
   */
  isOwner: boolean | null;
  /** From GET conversation detail `isShared`: other people take part, so it can be continued privately. */
  isShared?: boolean;
  /** From GET conversation detail `durable`: the session runs on the durable harness (LC2FIX), whatever its latest answer. */
  durable?: boolean;
  /**
   * Who the conversation is shared with: from the loaded detail, or from the
   * last share/unshare answer. `null` when not known.
   */
  sharedWith: SharedWithEntry[] | null;
  /**
   * Last known API `modelInfo` for this thread (from list/detail/SSE), used
   * to restore model + mode in the input when the user returns to this tab.
   */
  conversationModelInfo?: ModelInfo;
}

// ── Search types ──────────────────────────────────────────────────────

export interface SearchRequest {
  query: string;
  limit: number;
  filters: ChatKnowledgeFilters;
}

export interface SearchResultMetadata {
  orgId: string;
  recordId: string;
  virtualRecordId: string;
  recordName: string;
  recordType: string;
  recordVersion?: number;
  origin: string;
  connector: string;
  /** Connector instance the record came from; marks demo records. */
  connectorId?: string;
  connectorInstanceName?: string;
  blockText?: string;
  blockType?: string;
  bounding_box?: Array<{ x: number; y: number }>;
  pageNum?: (number | null)[];
  extension?: string;
  mimeType?: string;
  blockNum?: (number | null)[];
  webUrl?: string;
  previewRenderable?: boolean;
  hideWeburl?: boolean;
  kbId?: string | null;
  /** ISO time the record last synced. */
  updatedAt?: string;
  blockIndex?: number;
  isBlock?: boolean;
  isBlockGroup?: boolean;
  point_id?: string;
}

export interface SearchResultItem {
  score: number;
  citationType: string;
  metadata: SearchResultMetadata;
  content: string;
  block_type?: string;
  virtual_record_id?: string;
  block_index?: number;
  block_group_index?: number;
}

export interface UnindexedMatch {
  recordId: string;
  recordName: string;
  connector?: string | null;
  note?: string;
}

export interface SearchResponse {
  searchId: string;
  searchResponse: {
    searchResults: SearchResultItem[];
    /** The search store answered with less than its full search (e.g. it was busy). */
    searchDegraded?: boolean;
    accessNotices?: unknown;
  };
}
