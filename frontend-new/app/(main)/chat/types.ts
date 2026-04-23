import type { CitationOrigin } from './components/message-area/response-tabs/citations';
import type { CitationMaps } from './components/message-area/response-tabs/citations/types';
import type { ThreadMessageLike } from '@assistant-ui/react';

// Chat types following project conventions

// Confidence level for bot responses
export type ConfidenceLevel = 'Very High' | 'High' | 'Medium' | 'Low';

// Tab types for response view
export type ResponseTab = 'answer' | 'sources' | 'citation';

// Inline citation for display within answer text
export interface InlineCitation {
  id: string;
  label: string;      // e.g., "Sales 1", "Gmail 3"
  sourceType: string; // For icon/color mapping (slack, gmail, jira, etc.)
  sourceId?: string;  // Reference to source for scroll targeting
}

export interface ChatMessage {
  id: string;
  conversationId: string;
  role: 'user' | 'assistant';
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
  chatMode: string;
}

/** Entry in the sharedWith array from conversation API responses */
export interface SharedWithEntry {
  userId: string;
  accessLevel: string;
  _id: string;
}

export interface ConversationApiResponse {
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
}

export interface ConversationsListResponse {
  conversations: ConversationApiResponse[];
  sharedWithMeConversations: ConversationApiResponse[];
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
}

export interface ChatSuggestion {
  id: string;
  text: string;
  icons: Array<'slack' | 'jira' | 'notion' | 'sheets' | 'confluence' | 'github'>;
}

export type ChatMode = 'chat' | 'search';

/**
 * Query sub-modes selectable from the dropdown panel.
 * All requests use quick chatMode at the API; these control behavior/features.
 */
export type QueryMode = 'chat' | 'web-search' | 'image' | 'agent';

/** Strategy when query mode is Agent (toolbar dropdown). */
export type AgentStrategy = 'auto' | 'quick' | 'verify' | 'deep';

/**
 * Segment after `agent:` in the stream API body (differs from {@link AgentStrategy} for verify → verification).
 */
export type AgentStrategyApiSegment = 'auto' | 'quick' | 'verification' | 'deep';

/** API `chatMode` for streams: always `quick`, or `agent:<segment>` when in agent query mode. */
export type StreamChatModePayload = 'quick' | `agent:${AgentStrategyApiSegment}`;

/** Maps UI agent strategy to the API `agent:` segment (verify → verification). */
export function agentStrategyToApiSegment(strategy: AgentStrategy): AgentStrategyApiSegment {
  if (strategy === 'verify') return 'verification';
  return strategy;
}

/** Segments accepted by agent stream/regenerate HTTP bodies (`chatMode` field). */
const AGENT_HTTP_CHAT_MODES: readonly AgentStrategyApiSegment[] = [
  'auto',
  'quick',
  'verification',
  'deep',
];

/**
 * Map internal {@link StreamChatModePayload} to the plain `chatMode` string
 * expected by POST `/api/v1/agents/.../stream` (Python agent routes).
 */
export function streamChatModeToAgentApiChatMode(
  chatMode: StreamChatModePayload
): AgentStrategyApiSegment {
  if (chatMode === 'quick') return 'quick';
  if (typeof chatMode === 'string' && chatMode.startsWith('agent:')) {
    const rest = chatMode.slice(6) as AgentStrategyApiSegment;
    if (AGENT_HTTP_CHAT_MODES.includes(rest)) return rest;
  }
  return 'auto';
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
  iconType: 'material' | 'svg' | 'component';
  /** CSS variable references for this mode's color scheme */
  colors: {
    bg: string;     // Background for active state
    fg: string;     // Text/label color
    icon: string;   // Icon color
    toggle: string; // Active toggle/indicator color
  };
  /** Whether this mode is currently supported by the backend */
  enabled: boolean;
}

export interface ChatSettings {
  mode: ChatMode;
  queryMode: QueryMode;
  /** Used when queryMode is 'agent'. */
  agentStrategy: AgentStrategy;
  filters: {
    apps: string[];
    kb: string[];
  };
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
  availableModels: Record<string, { models: AvailableLlmModel[]; fetchedAt: number }>;
}

export interface UploadedFile {
  id: string;
  file: File;
  name: string;
  size: number;
  type: string;
  preview?: string;
}

export type SupportedFileType = 'TXT' | 'PDF' | 'DOCX' | 'PNG' | 'JPEG' | 'JPG';

// SSE Event Types
export type SSEEventType =
  | 'connected'
  | 'status'
  | 'answer_chunk'
  | 'complete'
  | 'tool_call'
  | 'tool_success'
  | 'artifact'
  | 'tool_error'
  /** Internal tool round-trip — UI ignores (same as legacy chat) */
  | 'tool_calls'
  /** Agent / deep flows — UI ignores */
  | 'tool_result'
  | 'metadata'
  | 'restreaming'
  | 'error';

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
}

export interface SSEConnectedEvent {
  message: string;
  /** Set by Node when a new conversation row is created before streaming (main + agent). */
  conversationId?: string;
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
    metadata: {
      recordName: string;
      recordId: string;
      connector: string;
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

export interface ConversationMessage {
  _id: string;
  messageType: 'user_query' | 'bot_response';
  content: string;
  contentFormat: 'MARKDOWN';
  citations: CitationApiResponse[];
  confidence?: 'Very High' | 'High' | 'Medium' | 'Low';
  followUpQuestions: string[];
  referenceData: ReferenceData[];
  modelInfo: ModelInfo;
  createdAt: string;
  updatedAt: string;
  feedback: Record<string, unknown>[];
}

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
}

export interface SSECompleteEvent {
  conversation: ConversationCompleteData;
  meta: {
    requestId: string;
    timestamp: string;
    duration: number;
  };
}

export interface SSEErrorEvent {
  error: string;
  message: string;
  code?: string;
}

// Status message for display during streaming
export interface StatusMessage {
  id: string;
  /** Mirrors SSE status when applicable (`connected`, `planning`, `executing`, …) */
  status: string;
  message: string;
  timestamp: string;
}

// Request payload for streaming API
export interface StreamChatRequest {
  query: string;
  modelKey: string;
  modelName: string;
  modelFriendlyName: string;
  /**
   * `quick` for normal modes; when query mode is Agent, `agent:<segment>`
   * e.g. `agent:auto`, `agent:verification` (UI “verify” strategy).
   */
  chatMode: StreamChatModePayload;
  filters: {
    apps: string[];
    kb: string[];
  };
  conversationId?: string;
  /** When set, the stream uses /api/v1/agents/:id/conversations/.../stream */
  agentId?: string;
  /**
   * Agent streams only → JSON `tools`: every enabled tool `fullName` (resolved from the
   * catalog when the UI means “all tools”; `[]` = none).
   */
  agentStreamTools?: string[];
}

/** Builds mode-related fields for stream/regenerate payloads from settings. */
export function buildStreamRequestModeFields(settings: ChatSettings): Pick<
  StreamChatRequest,
  'chatMode'
> {
  if (settings.queryMode === 'agent') {
    return {
      chatMode: `agent:${agentStrategyToApiSegment(settings.agentStrategy)}`,
    };
  }
  return {
    chatMode: 'quick',
  };
}

// ── Message Action Types ──

/** Model override for regenerate / edit query flows. */
export interface ModelOverride {
  modelKey: string;
  modelName: string;
  modelFriendlyName: string;
  /** Provider key from GET …/available/llm (e.g. `openAI`) — required for agent regenerate API */
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
}

/**
 * Transient UI state for the active message action indicator.
 * Lives in ChatInput (component-local, NOT in the Zustand store) to avoid
 * unnecessary selector evaluations and rerenders across unrelated components.
 */
export type ActiveMessageAction =
  | null
  | { type: 'regenerate'; messageId: string }
  | { type: 'editQuery'; messageId: string; text: string };

// ── Multi-Chat Slot Types ──

/** Maximum number of concurrent chat slots before LRU eviction. */
export const MAX_SLOTS = 15;

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
  /** True until the server assigns a real convId. */
  isTemp: boolean;
  /** True once messages have been loaded (or immediately for new chats). */
  isInitialized: boolean;
  /** True after first successful message load — drives tracker selection. */
  hasLoaded: boolean;

  /** Messages in assistant-ui ThreadMessageLike[] format. */
  messages: ThreadMessageLike[];

  // ── Per-slot streaming state ──
  isStreaming: boolean;
  streamingContent: string;
  streamingQuestion: string;
  currentStatusMessage: StatusMessage | null;
  streamingCitationMaps: CitationMaps | null;

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
  pendingCollections: Array<{ id: string; name: string }>;

  /** Artifacts produced during the current streaming response. */
  artifacts: ChatArtifact[];

  /** AbortController for the in-flight SSE stream (if any). */
  abortController: AbortController | null;

  /** Epoch ms of last interaction — used for LRU eviction. */
  lastAccessedAt: number;
}

// ── Search types ──────────────────────────────────────────────────────

export interface SearchFilters {
  departments: string[];
  moduleIds: string[];
  appSpecificRecordTypes: string[];
  apps: string[];
  kb: string[];
}

export interface SearchRequest {
  query: string;
  limit: number;
  filters: SearchFilters;
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

export interface SearchResponse {
  searchId: string;
  searchResponse: {
    searchResults: SearchResultItem[];
  };
}
