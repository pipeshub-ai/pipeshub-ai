import { create } from 'zustand';
import { debugLog } from './debug-logger';
import {
  forgetUnreadConversation,
  loadUnreadConversationIds,
  rememberUnreadConversation,
} from '@/lib/storage/chat-unread';
import { getCurrentUserId } from './utils/current-user-id';
import { orderPinnedFirst, withPinned } from './utils/pinned-conversations';
import { revalidateSwrNamespace, SWR_NS } from '@/lib/api/swr-cache';
import { onSessionEnd } from '@/lib/api/session-cache';
import { getConversationLastActivityMs } from '@/lib/utils/conversation-activity';
import {
  ChatKnowledgeFilters,
  CollectionMeta,
  Conversation,
  ChatMode,
  QueryMode,
  AgentStrategy,
  ChatSettings,
  ConversationsListResponse,
  ChatSlot,
  MAX_SLOTS,
  SearchResultItem,
  type ModelInfo,
  type PendingAskUserQuestion,
  type RegenAnswers,
  type AgentCapabilities,
  DEFAULT_AGENT_CAPABILITIES,
  normalizeReasoningEffort,
} from './types';
import type { RecordDetailsResponse } from '@/knowledge-base/types';
import type { PreviewCitation } from '@/app/components/file-preview/types';
import type { AgentSidebarRowMenuAccess } from './sidebar/agent-sidebar-row-access';
import type { ProjectSummary } from './project-types';
import { chatExtension } from '@/config';

// ── localStorage helpers for agent capabilities ──────────────────────

const LS_AGENT_CAPS_KEY = 'pipeshub-agent-capabilities';

function lsGetAgentCapabilities(agentId?: string): AgentCapabilities {
  if (typeof window === 'undefined') return { ...DEFAULT_AGENT_CAPABILITIES };
  try {
    const key = agentId ? `${LS_AGENT_CAPS_KEY}:${agentId}` : LS_AGENT_CAPS_KEY;
    const raw = localStorage.getItem(key);
    if (!raw) return { ...DEFAULT_AGENT_CAPABILITIES };
    const parsed = JSON.parse(raw) as Partial<AgentCapabilities>;
    // Only the known keys: a deep research choice saved before its toggle was removed must not be sent.
    return {
      internalSearch: typeof parsed.internalSearch === 'boolean' ? parsed.internalSearch : true,
      webSearch: typeof parsed.webSearch === 'boolean' ? parsed.webSearch : true,
    };
  } catch {
    return { ...DEFAULT_AGENT_CAPABILITIES };
  }
}

/**
 * Key under which a project chat keeps its own "+" menu capabilities, beside
 * the per-agent ones. Project chats used to send the global setting, so
 * turning Internal search off anywhere stopped them reading the project's files.
 */
export function projectCapabilitiesKey(projectId: string): string {
  return `project:${projectId}`;
}

/** The capabilities a project chat sends: its own choice, else everything on. */
export function projectAgentCapabilities(
  scoped: Record<string, AgentCapabilities>,
  projectId: string,
): AgentCapabilities {
  return scoped[projectCapabilitiesKey(projectId)] ?? { ...DEFAULT_AGENT_CAPABILITIES };
}

function lsSetAgentCapabilities(caps: AgentCapabilities, agentId?: string): void {
  if (typeof window === 'undefined') return;
  try {
    const key = agentId ? `${LS_AGENT_CAPS_KEY}:${agentId}` : LS_AGENT_CAPS_KEY;
    localStorage.setItem(key, JSON.stringify(caps));
  } catch {
    // Storage unavailable — silently ignore
  }
}

/** Row shape for the Actions / MCP tabs of the scoped resources panel (agent or project). */
export interface ScopedToolGroupRow {
  label: string;
  fullNames: string[];
  toolDescriptions?: Record<string, string>;
  /** Display name per `fullNames` entry, where the full name doesn't read as one (MCP). */
  toolLabels?: Record<string, string>;
  /** What a tool does to data, per `fullNames` entry, where it is known. */
  toolKinds?: Record<string, 'read' | 'write' | 'destructive'>;
  toolsetSlug: string;
  instanceId?: string;
  iconPath?: string;
  /** An MCP server's catalog type, which picks its logo. */
  typeId?: string;
}

/**
 * A connector row of a saved agent's or a project's sources. `nodeType` is set
 * on a record group, folder or record the source is limited to.
 */
export interface ScopedConnectorRow {
  id: string;
  label: string;
  connectorKind: string;
  nodeType?: string;
  /** For a node a source is limited to: the connector it belongs to. */
  source?: ScopedRowSource;
}

/** The connector or collection a limited row belongs to; the panel lists such rows under it. */
export interface ScopedRowSource {
  id: string;
  /** Empty when only the connector type is known (a project's scope). */
  label: string;
  kind: string;
}

/** A collection row of a saved agent's or a project's sources; see `ScopedConnectorRow`. */
export interface ScopedCollectionRow {
  id: string;
  name: string;
  sourceType?: string;
  nodeType?: string;
  source?: ScopedRowSource;
}

/** Composer allow-list derived from a project's settings (see `useProjectScopeHydration`). */
export interface ProjectChatScope {
  projectId: string;
  connectors: ScopedConnectorRow[];
  knowledgeCollectionRows: ScopedCollectionRow[];
  knowledgeDefaults: ChatKnowledgeFilters;
  /** The project's hidden Collection for uploads; the server always searches it. */
  linkedKnowledgeBaseId?: string | null;
  toolGroups: ScopedToolGroupRow[];
  mcpGroups: ScopedToolGroupRow[];
  /** Every selectable tool key (`instanceId:fullName`), across toolsets and MCP. */
  toolCatalogFullNames: string[];
}

/**
 * File preview state for citation preview in chat.
 */
export interface ChatPreviewFile {
  id: string;
  name: string;
  url: string;
  /**
   * Raw Blob for the streamed file. Populated only for renderers that are
   * better fed the binary data directly (DOCX via `docx-preview`) — avoids
   * the extra `URL.createObjectURL` + re-`fetch` roundtrip that was causing
   * the DOCX preview pane to stay blank.
   */
  blob?: Blob;
  type: string;
  size?: number;
  isLoading?: boolean;
  error?: string;
  recordDetails?: RecordDetailsResponse;
  /** Initial page to navigate to (from citation pageNum) */
  initialPage?: number;
  /** Bounding box for highlighting the cited region (normalized 0-1 coordinates) */
  highlightBox?: Array<{ x: number; y: number }>;
  /** Citations for the previewed record (used by the CitationsPanel) */
  citations?: PreviewCitation[];
  /**
   * Citation id the user actually clicked (from citation.citationId).
   * Used to seed the CitationsPanel so clicking `[2]` highlights citation [2],
   * not the first citation on the target page.
   */
  initialCitationId?: string;
  /**
   * External URL for the record (e.g. Jira ticket, Confluence page).
   * Used by UnknownPreview to open the source in a new browser tab.
   */
  webUrl?: string;
  /**
   * Whether a downloadable file is available for this record.
   * When false, the "Download File" button is hidden in UnknownPreview.
   */
  previewRenderable?: boolean;
  /**
   * Hide the "File Details" tab in the preview shell. Set for previews that
   * don't correspond to a KB record (e.g. chat-generated artifacts).
   */
  hideFileDetails?: boolean;
  /**
   * Show a "Download" button in the preview header. Enabled for previews
   * (e.g. chat attachments / artifacts) where the user is expected to be
   * able to save the file locally.
   */
  showDownload?: boolean;
  /**
   * Artifact registry version currently loaded into `url`/`blob`. Undefined
   * for previews that aren't version-tracked artifacts (citations, plain KB
   * records) — the version switcher only renders when this is set.
   */
  version?: number;
  /**
   * Highest version known to exist for this artifact (see `MessageList`'s
   * `latestArtifactVersions`). Drives the version-switcher dropdown's range;
   * equal to `version` when this IS the latest (no older versions to jump
   * back to, so the switcher stays non-interactive).
   */
  latestVersion?: number;
  /**
   * Re-fetches this artifact at `version` and replaces `url`/`blob`/`version`
   * in place via `setPreviewFile`. Bound by the caller (`chat-response.tsx`)
   * to whatever record/stream logic produced this preview in the first
   * place — the preview shell itself has no knowledge of artifacts.
   */
  onVersionChange?: (version: number) => void | Promise<void>;
  /**
   * True while `onVersionChange` is in flight. Kept separate from `isLoading`
   * so the previously-loaded content stays visible (and the switcher shows a
   * small spinner) instead of the whole panel flashing to a loading skeleton
   * on every version switch.
   */
  isSwitchingVersion?: boolean;
}

interface ConversationsPage {
  conversations: Conversation[];
  pagination: ConversationsListResponse['pagination'];
}

export interface SidebarConversationLists {
  owned: ConversationsPage;
  shared: ConversationsPage;
  excludeProjectChats: boolean;
}

/**
 * Temporary sidebar entry created when a new chat stream begins.
 *
 * Lifecycle:
 * 1. User sends message → addPendingConversation() creates entry with
 *    isGenerating=true, a timestamp ID, and title=null.
 * 2. Sidebar renders a shimmering "Generating Title…" until the SSE `connected`
 *    event includes `title` (same string persisted on the new conversation row),
 *    then shows that title with a spinner.
 * 3. SSE `complete` arrives → resolvePendingConversation() merges backend fields.
 *    If titles are refined async server-side later, list refresh / complete still wins.
 * 4. The conversation is prepended to the `conversations` array (Your Chats),
 *    and `pendingConversation` is cleared.
 *
 * NOTE: The pending item is ALWAYS pushed into "Your Chats" (time-grouped).
 * TODO: Handle Shared Chats — if the backend returns isShared=true on
 *       the complete event, we may need to move it to sharedConversations.
 */
export interface PendingConversation {
  /** The slotId that owns this pending entry — used for sidebar click-to-switch */
  slotId: string;
  /** Filled when SSE `connected` includes `title`, otherwise stays null until resolution completes */
  title: string | null;
  /** True while the stream that owns this pending row is still open */
  isGenerating: boolean;
  /** Epoch ms — used for time-group bucketing (always "Today") */
  createdAt: number;
}

/**
 * Pending sidebar rows for main chat vs agent chat — single place for agent isolation + dedup.
 */
export function selectPendingForSidebar(
  pendingBySlotId: Record<string, PendingConversation>,
  slots: Record<string, ChatSlot>,
  resolvedConvIds: ReadonlySet<string>,
  scope: 'global' | { agentId: string } | { projectId: string },
): PendingConversation[] {
  return Object.values(pendingBySlotId).filter((p) => {
    if (!p.isGenerating) return false;
    const slot = slots[p.slotId];
    if (!slot) return false;
    if (scope === 'global') {
      // Project-linked new chats surface in the project's own sidebar, not
      // the main "Your Chats" list.
      if (slot.threadAgentId || slot.projectId) return false;
    } else if ('agentId' in scope) {
      if (slot.threadAgentId !== scope.agentId) return false;
    } else if (slot.projectId !== scope.projectId) {
      return false;
    }
    if (slot.convId && resolvedConvIds.has(slot.convId)) return false;
    return true;
  });
}

/** True if any slot for this conversation is actively streaming in the given sidebar scope. */
export function isConversationStreamingInScope(
  slots: Record<string, ChatSlot>,
  conversationId: string,
  scopeAgentId: string | null,
): boolean {
  for (const slot of Object.values(slots)) {
    if (!slot.isStreaming || slot.convId !== conversationId) continue;
    if (scopeAgentId === null) {
      if (!slot.threadAgentId) return true;
    } else if (slot.threadAgentId === scopeAgentId) {
      return true;
    }
  }
  return false;
}

// ── Helper: create a default empty slot ─────────────────────────────

function createDefaultSlot(convId: string | null): ChatSlot {
  const isNew = convId === null;
  return {
    convId,
    threadAgentId: null,
    agentStreamTools: null,
    projectId: null,
    isTemp: isNew,
    isInitialized: isNew,      // new chats have nothing to load
    refreshGeneration: 0,
    hasLoaded: false,
    remoteRunInProgress: false,
    remoteRunId: null,
    remoteStopRequested: false,
    messages: [],
    isStreaming: false,
    streamingContent: '',
    streamingQuestion: '',
    currentStatusMessage: null,
    streamingCitationMaps: null,
    streamingParts: [],
    userScrollOverride: false,
    savedScrollTop: null,
    savedScrollWasStreaming: false,
    activeExpandedMessageId: null,
    regenerateMessageId: null,
    streamingAssistantId: null,
    pendingCollections: [],
    artifacts: [],
    pendingAskUserQuestion: null,
    abortController: null,
    runId: null,
    stopping: false,
    messagePagination: null,
    rawMessages: null,
    lastAccessedAt: Date.now(),
    isOwner: isNew ? true : null,
    sharedWith: isNew ? [] : null,
    ...chatExtension.slot.defaults(isNew),
  };
}

/** Whose saved unread chats are in memory; null until the first hydrate. */
let unreadOwnerId: string | null = null;

function withoutId(ids: Set<string>, id: string): Set<string> {
  if (!ids.has(id)) return ids;
  const next = new Set(ids);
  next.delete(id);
  return next;
}

// ── Helper: generate a stable slot ID ───────────────────────────────

let slotCounter = 0;
function sameKnowledgeFilters(a: ChatKnowledgeFilters, b: ChatKnowledgeFilters): boolean {
  return (['apps', 'kb', 'recordGroups', 'records', 'recordsExact'] as const).every((key) => {
    const left = new Set(a[key] ?? []);
    const right = b[key] ?? [];
    return left.size === new Set(right).size && right.every((id) => left.has(id));
  });
}

function generateSlotId(): string {
  slotCounter += 1;
  return `slot-${Date.now()}-${slotCounter}`;
}

// ── Store interface ─────────────────────────────────────────────────

interface ChatState {
  // ── Slot dictionary ──
  slots: Record<string, ChatSlot>;
  activeSlotId: string | null;

  // ── URL sync ──
  hasConsumedUrlNavigation: boolean;

  // ── Sidebar state (global, not per-slot) ──
  conversations: Conversation[];
  sharedConversations: Conversation[];
  isConversationsLoading: boolean;
  conversationsError: string | null;
  /**
   * When the rail's lists last arrived; null until the first load of this session. Only a rail with nothing
   * loaded yet shows a skeleton: later loads swap the rows in place.
   */
  conversationsLoadedAt: number | null;
  pagination: ConversationsListResponse['pagination'] | null;
  sharedPagination: ConversationsListResponse['pagination'] | null;
  /** Which Recents filter the lists above were fetched with; null until the first load. */
  sidebarListsExcludeProjectChats: boolean | null;
  pendingConversations: Record<string, PendingConversation>;
  /** IDs of conversations that just resolved from pending — triggers typing animation */
  newlyResolvedIds: Set<string>;
  /**
   * Conversations whose answer finished while the user was looking at something
   * else. Mirrored to localStorage per user so a reload keeps the dot.
   */
  unreadConversationIds: Set<string>;
  isMoreChatsPanelOpen: boolean;
  moreChatsSectionType: MoreChatsSection | null;
  /**
   * The rows of the open More chats panel. Kept here, not in the panel, so a delete, archive, rename or
   * pin made from any row (the rail's or the panel's) changes the panel too.
   */
  moreChats: MoreChatsList;
  /** What the More chats panel's search box holds, so the panel shown again (the drawer reopened) keeps it. */
  moreChatsSearch: string;
  /** Bumped after a mutation (rename/delete/archive) to trigger sidebar refetch */
  conversationsVersion: number;

  // ── Projects (sidebar list + active workspace) ──
  projects: ProjectSummary[];
  isProjectsLoading: boolean;
  projectsError: string | null;
  /** Bumped after create/update/delete/archive to trigger a sidebar refetch. */
  projectsVersion: number;
  /** When the Projects page's rail last loaded the list; null until then, and again after a sign-out. */
  projectsLoadedAt: number | null;
  /** `projectId` currently open in the workspace (`/chat?projectId=…`), or null. */
  activeProjectId: string | null;

  /** When set, agent sidebar lists + streaming prepends apply to this agent */
  agentSidebarAgentId: string | null;
  agentConversations: Conversation[];
  /** The agent's chats other people shared with the user, its rail's Shared chats. */
  agentSharedConversations: Conversation[];
  /**
   * Whose chats `agentConversations` and `agentSharedConversations` are. They outlive the agent's rail, so coming
   * back to the same agent shows them at once; another agent starts empty.
   */
  agentConversationsAgentId: string | null;
  agentConversationsPagination: ConversationsListResponse['pagination'] | null;
  isAgentConversationsLoading: boolean;
  agentConversationsError: string | null;
  isAgentMoreChatsPanelOpen: boolean;
  agentMoreChatsPagination: { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null;
  /**
   * Selected tool `fullName`s for the next agent stream. `null` = all tools (runtime expands
   * to `agentToolCatalogFullNames` in JSON `tools`). `[]` = none. Non-empty = explicit subset.
   */
  agentStreamTools: string[] | null;
  /** Every tool fullName from the loaded agent — drives the Actions tab and “select all”. */
  agentToolCatalogFullNames: string[];
  /** Connector rows scoped to the agent (for Connectors tab labels + icons). */
  agentChatConnectors: ScopedConnectorRow[];
  /** Distinct KB / collection ids the agent can use (Collections tab). */
  agentChatKbIds: string[];
  /** Agent KB rows with labels from the knowledge graph (record groups). */
  agentKnowledgeCollectionRows: ScopedCollectionRow[];
  /** Toolsets for grouped Actions UI (`label` prefers per-instance `instanceName`, else `displayName` / `name`). */
  agentChatToolGroups: Array<{
    label: string;
    fullNames: string[];
    /** Maps tool `fullName` → API description (for search / optional UI). */
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId?: string;
    iconPath?: string;
  }>;
  /** Attached MCP server groups for the MCP tab — same row shape as `agentChatToolGroups`. */
  agentChatMcpGroups: Array<{
    label: string;
    fullNames: string[];
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId?: string;
    iconPath?: string;
  }>;
  /** Default knowledge scope derived from the agent graph (used to reset UI). */
  agentKnowledgeDefaults: ChatKnowledgeFilters;
  /**
   * Optional override of connector / collection ids for agent streams. `null` means “use
   * `agentKnowledgeDefaults`” (same as the panel when nothing is narrowed); runtime still
   * sends explicit `filters` built from defaults or this scope.
   */
  agentKnowledgeScope: ChatKnowledgeFilters | null;
  /** Resolved agent name for the top chat header when `agentId` is in the URL */
  agentContextDisplayName: string | null;
  /** Whether the current scoped agent was configured with a web search provider. */
  agentHasWebSearch: boolean;
  /** The current scoped agent runs with its own credentials: the person has nothing of theirs to connect for it. */
  agentIsServiceAccount: boolean;
  /** Access flags (canEdit / showViewAgent / …) for the agent in context — drives the chat header menu */
  agentContextAccess: AgentSidebarRowMenuAccess | null;
  /** Tool display names marked deprecated on the last GET /agents/:id for the URL agent context. */
  agentDeprecatedToolNames: string[];

  // ── Project-scoped chat (`/chat?projectId=` or the /projects workspace composer) ──
  /**
   * Allow-list the composer may narrow within. `null` outside project context. Mirrors the
   * agent* fields above so `AgentScopedResourcesPanel` can render either source. The linked
   * hidden project KB is not listed — the server always includes it.
   */
  projectScope: ProjectChatScope | null;
  /** Per-turn narrowing of `projectScope.knowledgeDefaults`; `null` = whole project scope. */
  projectKnowledgeScope: ChatKnowledgeFilters | null;
  /** Project whose reopened conversation set `projectKnowledgeScope` before that project's scope loaded. */
  projectKnowledgeScopeRestoredFor: string | null;
  /** Per-turn narrowing of `projectScope.toolCatalogFullNames`; `null` = all project tools. */
  projectStreamTools: string[] | null;

  /** The reply the composer's Regenerate bar is open for; its question card can be answered again meanwhile. */
  regenTargetMessageId: string | null;
  /** Answers picked again on that card. `null` re-runs the reply with the answers it already has. */
  regenAnswers: RegenAnswers | null;

  // ── Universal agent mode (main chat, queryMode === 'agent', no agentId) ──
  /**
   * Selected tool `fullName`s for universal agent streams. `null` = all tools; `[]` = none; explicit
   * list = subset. Persists across turns in ASSISTANT_CTX and survives queryMode switches.
   */
  universalAgentStreamTools: string[] | null;
  /** Every tool fullName from my-toolsets — drives "select all" and null expansion. */
  universalAgentToolCatalogFullNames: string[];
  /**
   * Toolset groups for universal agent Actions tab (from GET /api/v1/toolsets/my-toolsets).
   * Keyed by `instanceId` where present; `isAuthenticated` drives credential status display.
   */
  universalAgentToolGroups: Array<{
    label: string;
    fullNames: string[];
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId: string;
    iconPath?: string;
    isAuthenticated: boolean;
  }>;
  universalAgentToolsLoading: boolean;
  universalAgentToolsError: string | null;
  /** Every tool fullName from my-mcp-servers (authenticated only) — kept separate from the
   * toolset catalog because the two load independently (paginated vs. one-shot); combine via
   * `getUniversalCombinedCatalog()` when computing "select all" semantics. */
  universalAgentMcpCatalogFullNames: string[];
  /** MCP server groups for universal agent MCP tab (from GET /api/v1/mcp-servers/my-mcp-servers). */
  universalAgentMcpGroups: Array<{
    label: string;
    fullNames: string[];
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId: string;
    iconPath?: string;
    isAuthenticated: boolean;
  }>;
  universalAgentMcpLoading: boolean;
  universalAgentMcpError: string | null;

  // ── Global settings (apply to all chats) ──
  settings: ChatSettings;

  // ── File preview (global — only one preview open at a time) ──
  previewFile: ChatPreviewFile | null;
  previewMode: 'sidebar' | 'fullscreen';

  // ── Expansion panel (global — applies to the active chat input) ──
  expansionViewMode: 'inline' | 'overlay';

  // ── Cache ──
  collectionNamesCache: Record<string, string>;
  collectionMetaCache: Record<string, CollectionMeta>;

  // ── Search state ──
  searchResults: SearchResultItem[];
  searchQuery: string;
  searchId: string | null;
  isSearching: boolean;
  searchError: string | null;
  /** Top-level fields of the last search response (opaque pass-through). */
  searchResponseMeta: Record<string, unknown> | null;

  // ── Slot actions ──
  /** Create a new slot. Returns the generated slotId. */
  createSlot: (convId: string | null) => string;
  /** Patch fields in a single slot. Only that slot's reference changes. */
  updateSlot: (slotId: string, patch: Partial<ChatSlot>) => void;
  /** Replace interactive questionnaire state for a slot (alias for `updateSlot`). */
  setPendingAskUserQuestion: (slotId: string, value: PendingAskUserQuestion | null) => void;
  /** Switch the active slot. Updates lastAccessedAt on the target. */
  setActiveSlot: (slotId: string) => void;
  /** Remove a slot from the dictionary entirely. */
  evictSlot: (slotId: string) => void;
  /**
   * Assign a real convId to a temp slot. After SSE `complete`, prefer calling
   * without `keepTemp` so `isTemp` becomes false. When the server sends
   * `conversationId` early in the `connected` event, use `keepTemp: true` so
   * URL "new chat" handling (`isTemp`) stays correct until the stream finishes.
   */
  resolveSlotConvId: (
    slotId: string,
    realConvId: string,
    options?: { keepTemp?: boolean }
  ) => void;
  /** Find a slot by its convId. O(n) scan but n ≤ MAX_SLOTS. */
  getSlotByConvId: (
    convId: string,
    opts?: { forAgentId: string | null }
  ) => { slotId: string; slot: ChatSlot } | null;
  /**
   * Clear the visible thread only. In-flight SSE for that slot (and all other
   * slots) keeps running — use this for New Chat / parallel conversations.
   */
  clearActiveSlot: () => void;
  /**
   * Make every cached slot of this conversation load its history again the next time it
   * is shown, now if it is open. A slot that is still streaming keeps its live state.
   */
  invalidateConversation: (convId: string) => void;

  // ── Sidebar actions ──
  /** Replaces Recents (owned + shared, page 1) with a server response. */
  setSidebarLists: (lists: SidebarConversationLists) => void;
  setIsConversationsLoading: (loading: boolean) => void;
  setConversationsError: (error: string | null) => void;
  toggleMoreChatsPanel: (sectionType: MoreChatsSection) => void;
  closeMoreChatsPanel: () => void;
  setMoreChatsSearch: (search: string) => void;
  /** Start the More chats list over (new open, section or search); returns the new list's key. */
  startMoreChatsList: () => number;
  /** A page of the More chats list arrived: page 1 replaces the rows, a later page appends new ids. */
  setMoreChatsPage: (key: number, rows: Conversation[], page: number, hasNextPage: boolean) => void;
  setMoreChatsLoadingMore: (key: number, loading: boolean) => void;
  /** A More chats load failed: the rows stay as they are. */
  endMoreChatsLoad: (key: number) => void;
  /** Where a row sits in the More chats list, taken before it is archived so Undo can put it back. */
  snapshotMoreChatsRow: (conversationId: string) => MoreChatsRowSnapshot | null;
  /** Put a row back at its former place in the More chats list, once, if that list is still showing. */
  restoreMoreChatsRow: (snapshot: MoreChatsRowSnapshot) => void;
  appendConversations: (convs: Conversation[]) => void;
  appendSharedConversations: (convs: Conversation[]) => void;
  /**
   * Puts the chat first in every rail list it is in. For a new message in it, pass `activity`: the chat then also takes
   * the message's time, in those lists and in an open More list, so Pinned (ordered by time) puts it first too. That
   * time is `activity.lastActivityAt`, the server's; when the server sent none, now or 1 ms past every other row's,
   * whichever is later. Without `activity` (opening a chat) the row only moves, and an open More list only takes a newer
   * time the rail already has.
   */
  moveConversationToTop: (conversationId: string, activity?: { lastActivityAt?: number }) => void;
  /**
   * After a follow-up stream completes, merge the latest `modelInfo` into
   * sidebar list rows. `moveConversationToTop` only reorders and would leave
   * a stale `modelInfo` on the row otherwise.
   */
  updateConversationModelInfoInLists: (
    conversationId: string,
    modelInfo: ModelInfo | undefined
  ) => void;
  /** Takes the row out of every list; `keepUnread` for a chat that still exists elsewhere (moved into a project). */
  removeConversation: (conversationId: string, options?: { keepUnread?: boolean }) => void;
  /** Where a row sits in each rail list, taken before it goes (an archive) so Undo can put it back without a reload. */
  snapshotRailRows: (conversationId: string) => RailRowsSnapshot;
  /** Puts a row back at its former place in each list it was in, once; the agent's lists only for the same agent. */
  restoreRailRows: (snapshot: RailRowsSnapshot) => void;
  /** Lists a row the rail does not have yet (a fork, a chat back from a project) where the server would: pinned first, then newest activity first. */
  insertConversationRow: (conversation: Conversation, list: 'conversations' | 'agentConversations') => void;
  markConversationUnread: (conversationId: string) => void;
  markConversationRead: (conversationId: string) => void;
  /** Bring back this user's unread chats from before a reload. */
  hydrateUnreadConversations: (userId: string) => void;
  renameConversation: (conversationId: string, newTitle: string) => void;
  /** Bump the version counter to trigger a sidebar refetch */
  bumpConversationsVersion: () => void;
  /** Forget every list the rails show (sign-out, another person signing in), so nobody sees someone else's chats. */
  clearSidebarLists: () => void;

  // ── Project actions ──
  setProjects: (projects: ProjectSummary[]) => void;
  setIsProjectsLoading: (loading: boolean) => void;
  setProjectsError: (error: string | null) => void;
  /** Insert-or-replace by `_id` (used after create/update/archive/pin). */
  upsertProjectInList: (project: ProjectSummary) => void;
  removeProjectFromList: (projectId: string) => void;
  bumpProjectsVersion: () => void;
  setActiveProjectId: (projectId: string | null) => void;
  /**
   * Optimistically reflects a project link/unlink in the sidebar conversation
   * lists (`conversations` / `agentConversations`) after
   * `ProjectApi.setConversationProject` succeeds, so the row moves without a
   * full refetch.
   */
  moveConversationToProject: (conversationId: string, projectId: string | null) => void;
  /**
   * Reflects the viewer's own pin/unpin straight away, pinned chats first, in
   * Your chats, Shared chats and an open More list; never on agent rows. An
   * unpinned chat goes back where its last activity puts it.
   * Returns the undo for a pin the server refused: the rows go back where they
   * were, not only unpinned.
   */
  setConversationPinned: (conversationId: string, pinned: boolean) => () => void;

  setAgentSidebarAgentId: (id: string | null) => void;
  setAgentConversations: (conversations: Conversation[]) => void;
  setAgentSharedConversations: (conversations: Conversation[]) => void;
  setAgentConversationsPagination: (pagination: ConversationsListResponse['pagination'] | null) => void;
  setIsAgentConversationsLoading: (loading: boolean) => void;
  setAgentConversationsError: (error: string | null) => void;
  toggleAgentMoreChatsPanel: () => void;
  closeAgentMoreChatsPanel: () => void;
  setAgentMoreChatsPagination: (p: { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null) => void;
  appendAgentConversations: (convs: Conversation[]) => void;
  setAgentStreamTools: (tools: string[] | null) => void;
  /** Reset catalog + knowledge UI when GET /agents/:id succeeds (or null to clear). */
  hydrateAgentChatResources: (payload: {
    toolCatalogFullNames: string[];
    toolGroups: Array<{
      /** Primary row title — instance label when present (matches agent builder). */
      label: string;
      fullNames: string[];
      toolDescriptions?: Record<string, string>;
      toolsetSlug: string;
      instanceId?: string;
      iconPath?: string;
    }>;
    /** Attached MCP server groups for the MCP tab. */
    mcpGroups: Array<{
      label: string;
      fullNames: string[];
      toolDescriptions?: Record<string, string>;
      toolsetSlug: string;
      instanceId?: string;
      iconPath?: string;
    }>;
    connectors: ScopedConnectorRow[];
    kbIds: string[];
    knowledgeCollectionRows: ScopedCollectionRow[];
    knowledgeDefaults: ChatKnowledgeFilters;
    deprecatedToolNames: string[];
    /** Whether the agent was built with a web search provider. */
    hasWebSearch?: boolean;
    /** The agent runs with its own credentials. */
    isServiceAccount?: boolean;
  } | null) => void;
  setAgentKnowledgeScope: (scope: ChatKnowledgeFilters | null) => void;
  /**
   * Replace the project allow-list (or clear with null). Resets per-turn narrowing and seeds
   * the collection name/meta caches so pills and `appliedFilters` resolve labels.
   */
  setProjectScope: (scope: ProjectChatScope | null) => void;
  setProjectKnowledgeScope: (scope: ChatKnowledgeFilters | null) => void;
  /** A reopened project conversation's last scope; survives that project's first `setProjectScope`. */
  restoreProjectKnowledgeScope: (projectId: string, scope: ChatKnowledgeFilters) => void;
  setProjectStreamTools: (tools: string[] | null) => void;
  setAgentContextDisplayName: (name: string | null) => void;
  setAgentContextAccess: (access: AgentSidebarRowMenuAccess | null) => void;

  setRegenTargetMessageId: (messageId: string | null) => void;
  setRegenAnswers: (answers: RegenAnswers | null) => void;

  // ── Universal agent actions ──
  setUniversalAgentStreamTools: (tools: string[] | null) => void;
  /** Populate universal agent tool groups from my-toolsets. Pass null to clear. */
  hydrateUniversalAgentResources: (payload: {
    toolGroups: Array<{
      label: string;
      fullNames: string[];
      toolDescriptions?: Record<string, string>;
      toolsetSlug: string;
      instanceId: string;
      iconPath?: string;
      isAuthenticated: boolean;
    }>;
    toolCatalogFullNames: string[];
  } | null) => void;
  setUniversalAgentToolsLoading: (loading: boolean) => void;
  setUniversalAgentToolsError: (error: string | null) => void;
  /** Populate universal agent MCP groups from my-mcp-servers. Pass null to clear. */
  hydrateUniversalAgentMcpResources: (payload: {
    mcpGroups: Array<{
      label: string;
      fullNames: string[];
      toolDescriptions?: Record<string, string>;
      toolsetSlug: string;
      instanceId: string;
      iconPath?: string;
      isAuthenticated: boolean;
    }>;
    mcpCatalogFullNames: string[];
  } | null) => void;
  setUniversalAgentMcpLoading: (loading: boolean) => void;
  setUniversalAgentMcpError: (error: string | null) => void;

  addPendingConversation: (slotId: string) => void;
  updatePendingConversationTitle: (slotId: string, title: string) => void;
  clearNewlyResolvedId: (conversationId: string) => void;
  resolvePendingConversation: (
    slotId: string,
    conversation: Conversation,
    options?: { isAgentStream?: boolean }
  ) => void;
  clearPendingConversation: (slotId: string) => void;

  // ── URL sync actions ──
  setHasConsumedUrlNavigation: (consumed: boolean) => void;

  // ── Preview actions ──
  setPreviewFile: (file: ChatPreviewFile | null) => void;
  setPreviewMode: (mode: 'sidebar' | 'fullscreen') => void;
  clearPreview: () => void;

  // ── Settings actions ──
  setMode: (mode: ChatMode) => void;
  setQueryMode: (queryMode: QueryMode) => void;
  setAgentStrategy: (agentStrategy: AgentStrategy) => void;
  setFilters: (filters: import('./types').ChatKnowledgeFilters) => void;
  setExpansionViewMode: (mode: 'inline' | 'overlay') => void;
  setSelectedModelForCtx: (ctxKey: string, model: import('./types').ModelOverride | null) => void;
  setDefaultModelForCtx: (ctxKey: string, model: import('./types').ModelOverride | null) => void;
  setAvailableModelsForCtx: (ctxKey: string, models: import('./types').AvailableLlmModel[]) => void;
  /**
   * Set the reasoning effort picked for a context (assistant or agent), for
   * this session only. `null` means "use the default" and clears the pick.
   */
  setReasoningEffortForCtx: (ctxKey: string, effort: import('./types').ReasoningEffort | null) => void;
  /** Store the agent-configured default reasoning effort for a context. */
  setAgentDefaultReasoningEffort: (ctxKey: string, effort: import('./types').ReasoningEffort | null) => void;
  setNewChatDefaultModel: (model: import('./types').ModelOverride | null) => void;
  /** Update universal agent mode capability toggles and persist to localStorage. */
  setAgentCapabilities: (caps: Partial<import('./types').AgentCapabilities>) => void;
  /**
   * Per-scoped-agent capability overrides (keyed by agentId).
   * Allows temporarily disabling a capability the agent has configured.
   * Stored in localStorage under `pipeshub-agent-capabilities:{agentId}`.
   */
  scopedAgentCapabilities: Record<string, import('./types').AgentCapabilities>;
  setScopedAgentCapabilities: (agentId: string, caps: Partial<import('./types').AgentCapabilities>) => void;

  // ── Search actions ──
  setSearchResults: (
    results: SearchResultItem[],
    searchId: string | null,
    query: string,
    responseMeta?: object | null,
  ) => void;
  setIsSearching: (loading: boolean) => void;
  setSearchError: (error: string | null) => void;
  clearSearchResults: () => void;

  // ── Cache actions ──
  setCollectionNamesCache: (cache: Record<string, string>) => void;
  setCollectionMetaCache: (cache: Record<string, CollectionMeta>) => void;

  // ── Global reset ──
  reset: () => void;
}

// ── Initial state ───────────────────────────────────────────────────

/** The rail section a More chats panel lists: every pinned chat, or the rest of Shared chats or Your chats. */
export type MoreChatsSection = 'pinned' | 'shared' | 'your';

/** The rows of the More chats panel and where its paging stands. */
export interface MoreChatsList {
  /**
   * Changes whenever the list starts over (another open, section or search), so a page or an Undo
   * meant for an earlier list is dropped.
   */
  key: number;
  rows: Conversation[];
  /** The last page loaded; 0 before the first. */
  page: number;
  hasNextPage: boolean;
  /** The first page is loading. */
  isLoading: boolean;
  /** A later page is loading. */
  isLoadingMore: boolean;
  /** When page 1 last arrived; 0 before it has. */
  loadedAt: number;
}

/** The rail lists a row can be in. */
type RailList = 'conversations' | 'sharedConversations' | 'agentConversations' | 'agentSharedConversations';
const RAIL_LISTS: RailList[] = ['conversations', 'sharedConversations', 'agentConversations', 'agentSharedConversations'];

/** A row and its place in each rail list, so Undo after an archive puts it back without a reload. */
export interface RailRowsSnapshot {
  conversationId: string;
  places: Array<{ list: RailList; index: number; row: Conversation }>;
  /** Whose the agent's lists were when the row left them. */
  agentId: string | null;
}

/** A More chats row and its place, so Undo after an archive can put it back where it was. */
export interface MoreChatsRowSnapshot {
  key: number;
  row: Conversation;
  index: number;
}

const EMPTY_MORE_CHATS: MoreChatsList = {
  key: 0,
  rows: [],
  page: 0,
  hasNextPage: false,
  isLoading: false,
  isLoadingMore: false,
  loadedAt: 0,
};

/** The list once its panel closes: no rows, and a new key so nothing late lands in it. */
function closedMoreChats(current: MoreChatsList): MoreChatsList {
  return { ...EMPTY_MORE_CHATS, key: current.key + 1 };
}

/** Apply `change` to the More chats rows; the same object back when nothing changed. */
function patchMoreChatsRows(
  current: MoreChatsList,
  change: (rows: Conversation[]) => Conversation[],
): MoreChatsList {
  if (current.rows.length === 0) return current;
  const rows = change(current.rows);
  if (rows.length === current.rows.length && rows.every((row, i) => row === current.rows[i])) return current;
  return { ...current, rows };
}

/**
 * The order the server lists your chats in: pinned first, then newest activity first. The More chats list takes it
 * after a pin or an unpin, so an unpinned chat goes back to its place, as the rail's list does once it reloads.
 * The sort is stable, so rows the server tied keep the order it sent them in.
 */
/**
 * A row of the open More list that neither rail list holds (past the rail's first page), and the rail list it belongs
 * to: Your chats for the viewer's own chats, Shared chats for the others.
 */
function moreRowMissingFromRail(
  state: Pick<ChatState, 'conversations' | 'sharedConversations' | 'moreChats' | 'moreChatsSectionType'>,
  conversationId: string,
): { list: 'conversations' | 'sharedConversations'; row: Conversation } | null {
  const listed = (rows: Conversation[]) => rows.some((c) => c.id === conversationId);
  if (listed(state.conversations) || listed(state.sharedConversations)) return null;
  const row = state.moreChats.rows.find((c) => c.id === conversationId);
  if (!row) return null;
  const shared = state.moreChatsSectionType === 'shared' || (state.moreChatsSectionType !== 'your' && row.isOwner === false);
  return { list: shared ? 'sharedConversations' : 'conversations', row };
}

function inServerOrder(rows: Conversation[]): Conversation[] {
  const rank = (c: Conversation) => (c.isPinned === true ? 0 : 1);
  return [...rows].sort((a, b) => rank(a) - rank(b) || getConversationLastActivityMs(b) - getConversationLastActivityMs(a));
}

const initialState = {
  slots: {} as Record<string, ChatSlot>,
  activeSlotId: null as string | null,
  hasConsumedUrlNavigation: false,

  conversations: [] as Conversation[],
  sharedConversations: [] as Conversation[],
  isConversationsLoading: false,
  conversationsError: null as string | null,
  conversationsLoadedAt: null as number | null,
  moreChatsSearch: '',
  pagination: null as ConversationsListResponse['pagination'] | null,
  sharedPagination: null as ConversationsListResponse['pagination'] | null,
  sidebarListsExcludeProjectChats: null as boolean | null,
  pendingConversations: {} as Record<string, PendingConversation>,
  newlyResolvedIds: new Set<string>(),
  unreadConversationIds: new Set<string>(),
  isMoreChatsPanelOpen: false,
  moreChatsSectionType: null as MoreChatsSection | null,
  moreChats: EMPTY_MORE_CHATS,
  conversationsVersion: 0,

  projects: [] as ProjectSummary[],
  isProjectsLoading: false,
  projectsError: null as string | null,
  projectsVersion: 0,
  projectsLoadedAt: null as number | null,
  activeProjectId: null as string | null,

  agentSidebarAgentId: null as string | null,
  agentConversations: [] as Conversation[],
  agentSharedConversations: [] as Conversation[],
  agentConversationsAgentId: null as string | null,
  agentConversationsPagination: null as ConversationsListResponse['pagination'] | null,
  isAgentConversationsLoading: false,
  agentConversationsError: null as string | null,
  isAgentMoreChatsPanelOpen: false,
  agentMoreChatsPagination: null as { page: number; hasNextPage: boolean; isLoadingMore: boolean } | null,
  agentStreamTools: null as string[] | null,
  agentToolCatalogFullNames: [] as string[],
  agentChatConnectors: [] as ScopedConnectorRow[],
  agentChatKbIds: [] as string[],
  agentKnowledgeCollectionRows: [] as ScopedCollectionRow[],
  agentChatToolGroups: [] as Array<{
    label: string;
    fullNames: string[];
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId?: string;
    iconPath?: string;
  }>,
  agentChatMcpGroups: [] as Array<{
    label: string;
    fullNames: string[];
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId?: string;
    iconPath?: string;
  }>,
  agentKnowledgeDefaults: { apps: [], kb: [] } as ChatKnowledgeFilters,
  agentKnowledgeScope: null as ChatKnowledgeFilters | null,
  agentContextDisplayName: null as string | null,
  agentHasWebSearch: false,
  agentIsServiceAccount: false,
  agentContextAccess: null as AgentSidebarRowMenuAccess | null,
  agentDeprecatedToolNames: [] as string[],

  projectScope: null as ProjectChatScope | null,
  projectKnowledgeScope: null as ChatKnowledgeFilters | null,
  projectKnowledgeScopeRestoredFor: null as string | null,
  projectStreamTools: null as string[] | null,

  regenTargetMessageId: null as string | null,
  regenAnswers: null as RegenAnswers | null,

  universalAgentStreamTools: null as string[] | null,
  universalAgentToolCatalogFullNames: [] as string[],
  universalAgentToolGroups: [] as Array<{
    label: string;
    fullNames: string[];
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId: string;
    iconPath?: string;
    isAuthenticated: boolean;
  }>,
  universalAgentToolsLoading: false,
  universalAgentToolsError: null as string | null,
  universalAgentMcpCatalogFullNames: [] as string[],
  universalAgentMcpGroups: [] as Array<{
    label: string;
    fullNames: string[];
    toolDescriptions?: Record<string, string>;
    toolsetSlug: string;
    instanceId: string;
    iconPath?: string;
    isAuthenticated: boolean;
  }>,
  universalAgentMcpLoading: false,
  universalAgentMcpError: null as string | null,

  settings: {
    mode: 'chat' as ChatMode,
    queryMode: 'agent' as QueryMode,
    agentStrategy: 'quick' as AgentStrategy,
    filters: {
      apps: [] as string[],
      kb: [] as string[],
    },
    agentCapabilities: lsGetAgentCapabilities(),
    selectedModels: {} as Record<string, import('./types').ModelOverride | null>,
    defaultModels: {} as Record<string, import('./types').ModelOverride | null>,
    availableModels: {} as Record<string, { models: import('./types').AvailableLlmModel[]; fetchedAt: number }>,
    reasoningEffort: {} as Record<string, import('./types').ReasoningEffort | null>,
    agentDefaultReasoningEffort: {} as Record<string, import('./types').ReasoningEffort | null>,
    newChatDefaultModel: null as import('./types').ModelOverride | null,
  },

  scopedAgentCapabilities: {} as Record<string, AgentCapabilities>,

  previewFile: null as ChatPreviewFile | null,
  previewMode: 'sidebar' as 'sidebar' | 'fullscreen',
  expansionViewMode: 'inline' as 'inline' | 'overlay',
  collectionNamesCache: {} as Record<string, string>,
  collectionMetaCache: {} as Record<string, CollectionMeta>,

  searchResults: [] as SearchResultItem[],
  searchQuery: '' as string,
  searchId: null as string | null,
  isSearching: false,
  searchError: null as string | null,
  searchResponseMeta: null as Record<string, unknown> | null,
};

// ── Store creation ──────────────────────────────────────────────────

export const useChatStore = create<ChatState>((set, get) => ({
  ...initialState,

  // ── Slot actions ─────────────────────────────────────────────────

  createSlot: (convId) => {
    const slotId = generateSlotId();
    const slot = createDefaultSlot(convId);

    set((state) => {
      let newSlots: Record<string, ChatSlot> = { ...state.slots, [slotId]: slot };

      const evictOne = (): boolean => {
        const ids = Object.keys(newSlots);
        if (ids.length <= MAX_SLOTS) return false;
        const pool = ids.filter(
          (id) =>
            id !== slotId &&
            id !== state.activeSlotId &&
            !chatExtension.slot.isOnScreen(newSlots[id], state.activeSlotId),
        );
        if (pool.length === 0) return false;
        const idleFirst = pool.filter((id) => !newSlots[id].isStreaming);
        const candidates = idleFirst.length > 0 ? idleFirst : pool;
        let victim: string | null = null;
        let lruTime = Infinity;
        for (const id of candidates) {
          const t = newSlots[id].lastAccessedAt;
          if (t < lruTime) {
            lruTime = t;
            victim = id;
          }
        }
        if (!victim) return false;
        newSlots[victim]?.abortController?.abort();
        const { [victim]: _, ...rest } = newSlots;
        newSlots = rest;
        return true;
      };

      while (Object.keys(newSlots).length > MAX_SLOTS) {
        if (!evictOne()) break;
      }

      return { slots: newSlots };
    });

    return slotId;
  },

  updateSlot: (slotId, patch) => {
    set((state) => {
      const existing = state.slots[slotId];
      if (!existing) return state;
      return {
        slots: {
          ...state.slots,
          [slotId]: { ...existing, ...patch },
        },
      };
    });
  },

  setPendingAskUserQuestion: (slotId, value) => {
    get().updateSlot(slotId, { pendingAskUserQuestion: value });
  },

  setActiveSlot: (slotId) => {
    const openedConvId = get().slots[slotId]?.convId;
    const wasUnread = Boolean(openedConvId && get().unreadConversationIds.has(openedConvId));
    set((state) => {
      const existing = state.slots[slotId];
      if (!existing) return state;
      return {
        activeSlotId: slotId,
        slots: {
          ...state.slots,
          [slotId]: { ...existing, lastAccessedAt: Date.now() },
        },
        // Opening a chat is what reads it, whichever sidebar row the user used.
        ...(existing.convId && state.unreadConversationIds.has(existing.convId)
          ? { unreadConversationIds: withoutId(state.unreadConversationIds, existing.convId) }
          : {}),
      };
    });
    if (openedConvId && wasUnread) forgetUnreadConversation(getCurrentUserId(), openedConvId);
  },

  evictSlot: (slotId) => {
    set((state) => {
      const slot = state.slots[slotId];
      if (!slot) return state;
      // Abort any in-flight stream
      slot.abortController?.abort();
      const newSlots = { ...state.slots };
      delete newSlots[slotId];
      const hadPending = Object.prototype.hasOwnProperty.call(state.pendingConversations, slotId);
      const { [slotId]: _pending, ...pendingRest } = state.pendingConversations;
      return {
        slots: newSlots,
        activeSlotId: state.activeSlotId === slotId ? null : state.activeSlotId,
        pendingConversations: hadPending ? pendingRest : state.pendingConversations,
      };
    });
  },

  resolveSlotConvId: (slotId, realConvId, options) => {
    set((state) => {
      const existing = state.slots[slotId];
      if (!existing) return state;
      const keepTemp = options?.keepTemp === true;
      return {
        slots: {
          ...state.slots,
          [slotId]: {
            ...existing,
            convId: realConvId,
            isTemp: keepTemp ? true : false,
          },
        },
      };
    });
  },

  invalidateConversation: (convId) => {
    set((state) => {
      let changed = false;
      const slots = { ...state.slots };
      for (const [slotId, slot] of Object.entries(state.slots)) {
        if (slot.convId !== convId) continue;
        if (slot.isTemp || slot.isStreaming || slot.stopping) continue;
        // A load already in flight (isInitialized false) must not swallow this: the bump makes it
        // discard its now-older response and fetch again.
        slots[slotId] = { ...slot, isInitialized: false, refreshGeneration: slot.refreshGeneration + 1 };
        changed = true;
      }
      return changed ? { slots } : state;
    });
  },

  getSlotByConvId: (convId, opts) => {
    const { slots } = get();
    for (const [slotId, slot] of Object.entries(slots)) {
      if (slot.convId !== convId) continue;
      if (chatExtension.slot.isRetired(slot)) continue;
      if (opts === undefined) {
        return { slotId, slot };
      }
      const want = opts.forAgentId;
      if (want == null || want === '') {
        if (!slot.threadAgentId) return { slotId, slot };
      } else if (slot.threadAgentId === want) {
        return { slotId, slot };
      }
    }
    return null;
  },

  clearActiveSlot: () => set({ activeSlotId: null }),

  // ── Sidebar actions ──────────────────────────────────────────────

  setSidebarLists: ({ owned, shared, excludeProjectChats }) =>
    set({
      conversations: owned.conversations,
      sharedConversations: shared.conversations,
      pagination: owned.pagination,
      sharedPagination: shared.pagination,
      sidebarListsExcludeProjectChats: excludeProjectChats,
      conversationsLoadedAt: Date.now(),
    }),

  setIsConversationsLoading: (loading) => set({ isConversationsLoading: loading }),

  setConversationsError: (error) => set({ conversationsError: error }),

  toggleMoreChatsPanel: (sectionType) =>
    set((state) => {
      if (state.isMoreChatsPanelOpen && state.moreChatsSectionType === sectionType) {
        return {
          isMoreChatsPanelOpen: false,
          moreChatsSectionType: null,
          moreChats: closedMoreChats(state.moreChats),
          moreChatsSearch: '',
        };
      }
      // The list starts fresh each time the panel opens, loading until its first page arrives, with an empty search
      return {
        isMoreChatsPanelOpen: true,
        moreChatsSectionType: sectionType,
        moreChats: { ...EMPTY_MORE_CHATS, key: state.moreChats.key + 1, isLoading: true },
        moreChatsSearch: '',
        isAgentMoreChatsPanelOpen: false,
        agentMoreChatsPagination: null,
      };
    }),

  closeMoreChatsPanel: () =>
    set((state) => ({
      isMoreChatsPanelOpen: false,
      moreChatsSectionType: null,
      moreChats: closedMoreChats(state.moreChats),
      moreChatsSearch: '',
    })),

  setMoreChatsSearch: (search) => set({ moreChatsSearch: search }),

  // Resets only the sidebar's own state. The agent's tools, knowledge, name and access belong to
  // the chat page, which sets and clears them from the agent detail as the URL's agent changes.
  setAgentSidebarAgentId: (id) =>
    set((state) => {
      // Resets only the rail's own state; its lists stay (`agentConversationsAgentId` says whose they are). The
      // agent's tools, knowledge, name and access belong to the chat page, which sets and clears them from the agent
      // detail as the URL's agent changes.
      const context = {
        isAgentMoreChatsPanelOpen: false,
        agentMoreChatsPagination: null,
      };
      if (id === null) {
        return { agentSidebarAgentId: null, agentConversationsError: null, ...context };
      }
      if (state.agentSidebarAgentId === id) {
        return state;
      }
      const lists =
        state.agentConversationsAgentId === id
          ? {}
          : {
              agentConversations: [],
              agentSharedConversations: [],
              agentConversationsAgentId: null,
              agentConversationsPagination: null,
            };
      return { agentSidebarAgentId: id, agentConversationsError: null, ...lists, ...context };
    }),

  setAgentConversations: (conversations) => set({ agentConversations: conversations }),

  setAgentSharedConversations: (conversations) => set({ agentSharedConversations: conversations }),

  setAgentConversationsPagination: (pagination) => set({ agentConversationsPagination: pagination }),

  setIsAgentConversationsLoading: (loading) => set({ isAgentConversationsLoading: loading }),

  setAgentConversationsError: (error) => set({ agentConversationsError: error }),

  toggleAgentMoreChatsPanel: () =>
    set((state) => {
      if (state.isAgentMoreChatsPanelOpen) {
        return { isAgentMoreChatsPanelOpen: false, agentMoreChatsPagination: null };
      }
      return {
        isAgentMoreChatsPanelOpen: true,
        agentMoreChatsPagination: null,
        isMoreChatsPanelOpen: false,
        moreChatsSectionType: null,
        moreChats: closedMoreChats(state.moreChats),
      };
    }),

  closeAgentMoreChatsPanel: () =>
    set({ isAgentMoreChatsPanelOpen: false, agentMoreChatsPagination: null }),

  setAgentMoreChatsPagination: (p) => set({ agentMoreChatsPagination: p }),

  startMoreChatsList: () => {
    const key = get().moreChats.key + 1;
    set({ moreChats: { ...EMPTY_MORE_CHATS, key, isLoading: true } });
    return key;
  },

  setMoreChatsPage: (key, rows, page, hasNextPage) =>
    set((state) => {
      const current = state.moreChats;
      if (current.key !== key) return state;
      let nextRows = rows;
      if (page > 1) {
        const seen = new Set(current.rows.map((c) => c.id));
        nextRows = [...current.rows, ...rows.filter((c) => !seen.has(c.id))];
      }
      return {
        moreChats: {
          ...current,
          rows: nextRows,
          page,
          hasNextPage,
          isLoading: false,
          isLoadingMore: false,
          loadedAt: page === 1 ? Date.now() : current.loadedAt,
        },
      };
    }),

  setMoreChatsLoadingMore: (key, loading) =>
    set((state) =>
      state.moreChats.key !== key || state.moreChats.isLoadingMore === loading
        ? state
        : { moreChats: { ...state.moreChats, isLoadingMore: loading } },
    ),

  endMoreChatsLoad: (key) =>
    set((state) =>
      state.moreChats.key !== key ? state : { moreChats: { ...state.moreChats, isLoading: false, isLoadingMore: false } },
    ),

  snapshotMoreChatsRow: (conversationId) => {
    const { moreChats } = get();
    const index = moreChats.rows.findIndex((c) => c.id === conversationId);
    return index < 0 ? null : { key: moreChats.key, row: moreChats.rows[index], index };
  },

  restoreMoreChatsRow: ({ key, row, index }) =>
    set((state) => {
      const current = state.moreChats;
      if (current.key !== key || current.rows.some((c) => c.id === row.id)) return state;
      const rows = [...current.rows];
      rows.splice(Math.min(index, rows.length), 0, row);
      return { moreChats: { ...current, rows } };
    }),

  appendConversations: (convs) =>
    set((state) => {
      const existingIds = new Set(state.conversations.map((c) => c.id));
      const newOnes = convs.filter((c) => !existingIds.has(c.id));
      return { conversations: [...state.conversations, ...newOnes] };
    }),

  appendSharedConversations: (convs) =>
    set((state) => {
      const existingIds = new Set(state.sharedConversations.map((c) => c.id));
      const newOnes = convs.filter((c) => !existingIds.has(c.id));
      return { sharedConversations: [...state.sharedConversations, ...newOnes] };
    }),

  appendAgentConversations: (convs) =>
    set((state) => {
      const existingIds = new Set(state.agentConversations.map((c) => c.id));
      const newOnes = convs.filter((c) => !existingIds.has(c.id));
      return { agentConversations: [...state.agentConversations, ...newOnes] };
    }),

  setAgentStreamTools: (tools) => set({ agentStreamTools: tools }),

  hydrateAgentChatResources: (payload) =>
    set((state) =>
      payload
        ? {
            agentToolCatalogFullNames: payload.toolCatalogFullNames,
            agentChatToolGroups: payload.toolGroups,
            agentChatMcpGroups: payload.mcpGroups,
            agentChatConnectors: payload.connectors,
            agentChatKbIds: payload.kbIds,
            agentKnowledgeCollectionRows: payload.knowledgeCollectionRows,
            agentKnowledgeDefaults: payload.knowledgeDefaults,
            agentDeprecatedToolNames: payload.deprecatedToolNames,
            agentHasWebSearch: payload.hasWebSearch ?? false,
            agentIsServiceAccount: payload.isServiceAccount ?? false,
            agentKnowledgeScope: null,
            agentStreamTools: null,
            collectionNamesCache: (() => {
              const patch: Record<string, string> = {};
              for (const r of payload.knowledgeCollectionRows) {
                patch[r.id] = r.name;
              }
              return { ...state.collectionNamesCache, ...patch };
            })(),
            collectionMetaCache: (() => {
              const patch: Record<string, CollectionMeta> = {};
              for (const c of payload.connectors) {
                patch[c.id] = { name: c.label, nodeType: c.nodeType ?? 'app', connector: c.connectorKind };
              }
              for (const r of payload.knowledgeCollectionRows) {
                patch[r.id] = { name: r.name, nodeType: r.nodeType ?? 'recordGroup', connector: r.sourceType ?? 'KB' };
              }
              return { ...state.collectionMetaCache, ...patch };
            })(),
          }
        : {
            agentToolCatalogFullNames: [],
            agentChatToolGroups: [],
            agentChatMcpGroups: [],
            agentChatConnectors: [],
            agentChatKbIds: [],
            agentKnowledgeCollectionRows: [],
            agentKnowledgeDefaults: { apps: [], kb: [] },
            agentDeprecatedToolNames: [],
            agentHasWebSearch: false,
            agentIsServiceAccount: false,
            agentKnowledgeScope: null,
            agentStreamTools: null,
          }
    ),

  setAgentKnowledgeScope: (scope) => set({ agentKnowledgeScope: scope }),

  setProjectScope: (scope) =>
    set((state) => {
      if (!scope) {
        return {
          projectScope: null,
          projectKnowledgeScope: null,
          projectKnowledgeScopeRestoredFor: null,
          projectStreamTools: null,
        };
      }
      const names: Record<string, string> = {};
      const meta: Record<string, CollectionMeta> = {};
      for (const c of scope.connectors) {
        names[c.id] = c.label;
        meta[c.id] = { name: c.label, nodeType: c.nodeType ?? 'app', connector: c.connectorKind };
      }
      for (const r of scope.knowledgeCollectionRows) {
        names[r.id] = r.name;
        meta[r.id] = { name: r.name, nodeType: r.nodeType ?? 'recordGroup', connector: r.sourceType ?? 'KB' };
      }
      // Same project re-hydrated (settings saved, catalog arrived, a new chat resolved): keep the
      // user's per-turn narrowing, minus anything no longer in the allow-list. A different project
      // starts clean, unless one of its conversations was just reopened.
      const sameProject = state.projectScope?.projectId === scope.projectId;
      const restored = state.projectKnowledgeScopeRestoredFor === scope.projectId;
      let projectKnowledgeScope: ChatKnowledgeFilters | null = null;
      let projectStreamTools: string[] | null = null;
      const narrowedBelowApps =
        Boolean(state.projectKnowledgeScope?.recordGroups?.length) ||
        Boolean(state.projectKnowledgeScope?.records?.length);
      const knowledgeUnchanged =
        sameProject && sameKnowledgeFilters(state.projectScope!.knowledgeDefaults, scope.knowledgeDefaults);
      if (restored && state.projectKnowledgeScope) {
        // The server bounds the nodes to the project; a scope equal to the project's own is no narrowing.
        projectKnowledgeScope = sameKnowledgeFilters(state.projectKnowledgeScope, scope.knowledgeDefaults)
          ? null
          : state.projectKnowledgeScope;
      } else if (sameProject && state.projectKnowledgeScope && narrowedBelowApps) {
        // Folders and records may sit under a source the project dropped; the server
        // bounds them otherwise, so only a changed project scope starts again.
        projectKnowledgeScope = knowledgeUnchanged ? state.projectKnowledgeScope : null;
      } else if (sameProject && state.projectKnowledgeScope) {
        const apps = new Set(scope.knowledgeDefaults.apps);
        const kb = new Set(scope.knowledgeDefaults.kb);
        projectKnowledgeScope = {
          apps: state.projectKnowledgeScope.apps.filter((id) => apps.has(id)),
          kb: state.projectKnowledgeScope.kb.filter((id) => kb.has(id)),
        };
      }
      if (sameProject && state.projectStreamTools) {
        const catalog = new Set(scope.toolCatalogFullNames);
        projectStreamTools = state.projectStreamTools.filter((fn) => catalog.has(fn));
      }
      return {
        projectScope: scope,
        projectKnowledgeScope,
        projectKnowledgeScopeRestoredFor: null,
        projectStreamTools,
        collectionNamesCache: { ...state.collectionNamesCache, ...names },
        collectionMetaCache: { ...state.collectionMetaCache, ...meta },
      };
    }),

  setProjectKnowledgeScope: (scope) => set({ projectKnowledgeScope: scope }),

  restoreProjectKnowledgeScope: (projectId, scope) =>
    set((state) => ({
      projectKnowledgeScope: scope,
      projectKnowledgeScopeRestoredFor: state.projectScope?.projectId === projectId ? null : projectId,
    })),

  setProjectStreamTools: (tools) => set({ projectStreamTools: tools }),

  setAgentContextDisplayName: (name) => set({ agentContextDisplayName: name }),

  setAgentContextAccess: (access) => set({ agentContextAccess: access }),

  setRegenTargetMessageId: (messageId) => set({ regenTargetMessageId: messageId }),
  setRegenAnswers: (answers) => set({ regenAnswers: answers }),

  setUniversalAgentStreamTools: (tools) => set({ universalAgentStreamTools: tools }),

  hydrateUniversalAgentResources: (payload) =>
    set(
      payload
        ? {
            universalAgentToolGroups: payload.toolGroups,
            universalAgentToolCatalogFullNames: payload.toolCatalogFullNames,
            universalAgentToolsLoading: false,
            universalAgentToolsError: null,
          }
        : {
            universalAgentToolGroups: [],
            universalAgentToolCatalogFullNames: [],
            universalAgentStreamTools: null,
            universalAgentToolsLoading: false,
            universalAgentToolsError: null,
          }
    ),

  setUniversalAgentToolsLoading: (loading) => set({ universalAgentToolsLoading: loading }),

  setUniversalAgentToolsError: (error) => set({ universalAgentToolsError: error }),

  hydrateUniversalAgentMcpResources: (payload) =>
    set(
      payload
        ? {
            universalAgentMcpGroups: payload.mcpGroups,
            universalAgentMcpCatalogFullNames: payload.mcpCatalogFullNames,
            universalAgentMcpLoading: false,
            universalAgentMcpError: null,
          }
        : {
            universalAgentMcpGroups: [],
            universalAgentMcpCatalogFullNames: [],
            universalAgentMcpLoading: false,
            universalAgentMcpError: null,
          }
    ),

  setUniversalAgentMcpLoading: (loading) => set({ universalAgentMcpLoading: loading }),

  setUniversalAgentMcpError: (error) => set({ universalAgentMcpError: error }),

  moveConversationToTop: (conversationId, activity) =>
    set((state) => {
      const time = getConversationLastActivityMs;
      const listed = [...state.conversations, ...state.sharedConversations, ...state.agentConversations];
      const [own] = listed.filter((c) => c.id === conversationId).sort((a, b) => time(b) - time(a));
      if (!own) return state;
      let at = own.lastActivityAt;
      if (activity) {
        // The server's time keeps Pinned in the server's order whatever this tab's clock says. Copied raw: a
        // `lastActivityAt` under 1e12 is read as seconds.
        const sent = activity.lastActivityAt;
        const others = [...listed, ...state.moreChats.rows].filter((c) => c.id !== conversationId);
        at = typeof sent === 'number' && sent > 0 ? sent : Math.max(Date.now(), Math.max(0, ...others.map(time)) + 1);
      }
      // Rail rows change time only for a new message. An open More list's rows also take a newer time the rail has (a
      // new message seen live sets the rail's first).
      const touch = (c: Conversation) =>
        c.id !== conversationId || c.lastActivityAt === at || (!activity && time(c) >= time(own))
          ? c
          : { ...c, lastActivityAt: at };
      const toTop = (list: Conversation[]) => {
        const idx = list.findIndex((c) => c.id === conversationId);
        if (idx < 0) return list;
        const row = activity ? touch(list[idx]) : list[idx];
        return idx === 0 && row === list[0] ? list : [row, ...list.slice(0, idx), ...list.slice(idx + 1)];
      };
      const next = {
        conversations: toTop(state.conversations),
        sharedConversations: toTop(state.sharedConversations),
        agentConversations: toTop(state.agentConversations),
        moreChats: patchMoreChatsRows(state.moreChats, (rows) => rows.map(touch)),
      };
      if (
        next.conversations === state.conversations &&
        next.sharedConversations === state.sharedConversations &&
        next.agentConversations === state.agentConversations &&
        next.moreChats === state.moreChats
      ) {
        return state;
      }
      return next;
    }),

  updateConversationModelInfoInLists: (conversationId, modelInfo) => {
    if (!modelInfo) return;
    set((state) => {
      const patch = (list: Conversation[]) => {
        if (!list.some((c) => c.id === conversationId)) return list;
        return list.map((c) =>
          c.id === conversationId ? { ...c, modelInfo } : c
        );
      };
      const nextC = patch(state.conversations);
      const nextS = patch(state.sharedConversations);
      const nextA = patch(state.agentConversations);
      if (
        nextC === state.conversations &&
        nextS === state.sharedConversations &&
        nextA === state.agentConversations
      ) {
        return state;
      }
      return {
        conversations: nextC,
        sharedConversations: nextS,
        agentConversations: nextA,
      };
    });
  },

  removeConversation: (conversationId, options) => {
    const forgetUnread = !options?.keepUnread && get().unreadConversationIds.has(conversationId);
    set((state) => ({
      conversations: state.conversations.filter((c) => c.id !== conversationId),
      sharedConversations: state.sharedConversations.filter((c) => c.id !== conversationId),
      agentConversations: state.agentConversations.filter((c) => c.id !== conversationId),
      agentSharedConversations: state.agentSharedConversations.filter((c) => c.id !== conversationId),
      moreChats: patchMoreChatsRows(state.moreChats, (rows) => rows.filter((c) => c.id !== conversationId)),
      ...(forgetUnread ? { unreadConversationIds: withoutId(state.unreadConversationIds, conversationId) } : {}),
    }));
    if (forgetUnread) forgetUnreadConversation(getCurrentUserId(), conversationId);
  },

  snapshotRailRows: (conversationId) => {
    const state = get();
    const places = RAIL_LISTS.flatMap((list) => {
      const index = state[list].findIndex((c) => c.id === conversationId);
      return index < 0 ? [] : [{ list, index, row: state[list][index] }];
    });
    return { conversationId, places, agentId: state.agentConversationsAgentId };
  },

  restoreRailRows: ({ conversationId, places, agentId }) =>
    set((state) => {
      const patch: Partial<Record<RailList, Conversation[]>> = {};
      for (const { list, index, row } of places) {
        const agentList = list === 'agentConversations' || list === 'agentSharedConversations';
        if ((agentList && state.agentConversationsAgentId !== agentId) || state[list].some((c) => c.id === conversationId)) {
          continue;
        }
        const rows = [...state[list]];
        rows.splice(Math.min(index, rows.length), 0, row);
        patch[list] = rows;
      }
      return patch;
    }),

  insertConversationRow: (conversation, list) =>
    set((state) => {
      const rows = state[list].filter((c) => c.id !== conversation.id);
      const rank = (c: Conversation) => (c.isPinned === true ? 0 : 1);
      const activity = getConversationLastActivityMs(conversation);
      const at = rows.findIndex(
        (c) => rank(c) > rank(conversation) || (rank(c) === rank(conversation) && getConversationLastActivityMs(c) <= activity),
      );
      rows.splice(at < 0 ? rows.length : at, 0, conversation);
      return { [list]: rows };
    }),

  markConversationUnread: (conversationId) => {
    if (!get().unreadConversationIds.has(conversationId)) {
      set((state) => {
        const next = new Set(state.unreadConversationIds);
        next.add(conversationId);
        return { unreadConversationIds: next };
      });
    }
    // Written even when already unread, which refreshes how long it is kept.
    rememberUnreadConversation(getCurrentUserId(), conversationId);
  },

  markConversationRead: (conversationId) => {
    if (!get().unreadConversationIds.has(conversationId)) return;
    set((state) => ({ unreadConversationIds: withoutId(state.unreadConversationIds, conversationId) }));
    forgetUnreadConversation(getCurrentUserId(), conversationId);
  },

  hydrateUnreadConversations: (userId) => {
    const stored = loadUnreadConversationIds(userId);
    // Chats flagged before this ran belong to the same person on a first hydrate;
    // after a switch to someone else they must not carry over.
    const keepCurrent = unreadOwnerId === null || unreadOwnerId === userId;
    unreadOwnerId = userId;
    set((state) => {
      const next = new Set(stored);
      if (keepCurrent) state.unreadConversationIds.forEach((id) => next.add(id));
      const unchanged = next.size === state.unreadConversationIds.size
        && [...next].every((id) => state.unreadConversationIds.has(id));
      return unchanged ? state : { unreadConversationIds: next };
    });
  },

  renameConversation: (conversationId, newTitle) =>
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === conversationId ? { ...c, title: newTitle } : c
      ),
      sharedConversations: state.sharedConversations.map((c) =>
        c.id === conversationId ? { ...c, title: newTitle } : c
      ),
      agentConversations: state.agentConversations.map((c) =>
        c.id === conversationId ? { ...c, title: newTitle } : c
      ),
      agentSharedConversations: state.agentSharedConversations.map((c) =>
        c.id === conversationId ? { ...c, title: newTitle } : c
      ),
      moreChats: patchMoreChatsRows(state.moreChats, (rows) =>
        rows.map((c) => (c.id === conversationId ? { ...c, title: newTitle } : c))
      ),
    })),

  bumpConversationsVersion: () => {
    set((state) => ({ conversationsVersion: state.conversationsVersion + 1 }));
    revalidateSwrNamespace(SWR_NS.conversationLists);
  },

  clearSidebarLists: () =>
    set((state) => ({
      conversations: [],
      sharedConversations: [],
      pagination: null,
      sharedPagination: null,
      isConversationsLoading: false,
      conversationsError: null,
      conversationsLoadedAt: null,
      isMoreChatsPanelOpen: false,
      moreChatsSectionType: null,
      moreChats: closedMoreChats(state.moreChats),
      moreChatsSearch: '',
      agentConversations: [],
      agentSharedConversations: [],
      agentConversationsAgentId: null,
      agentConversationsPagination: null,
      agentConversationsError: null,
      isAgentConversationsLoading: false,
      isAgentMoreChatsPanelOpen: false,
      agentMoreChatsPagination: null,
      projects: [],
      projectsError: null,
      isProjectsLoading: false,
      projectsLoadedAt: null,
    })),

  // ── Project actions ──────────────────────────────────────────────

  setProjects: (projects) => set({ projects }),
  setIsProjectsLoading: (loading) => set({ isProjectsLoading: loading }),
  setProjectsError: (error) => set({ projectsError: error }),

  upsertProjectInList: (project) =>
    set((state) => {
      const exists = state.projects.some((p) => p._id === project._id);
      return {
        projects: exists
          ? state.projects.map((p) => (p._id === project._id ? project : p))
          : [project, ...state.projects],
      };
    }),

  removeProjectFromList: (projectId) =>
    set((state) => ({
      projects: state.projects.filter((p) => p._id !== projectId),
    })),

  bumpProjectsVersion: () =>
    set((state) => ({ projectsVersion: state.projectsVersion + 1 })),

  setActiveProjectId: (projectId) => set({ activeProjectId: projectId }),

  moveConversationToProject: (conversationId, projectId) =>
    set((state) => {
      const patch = (c: Conversation): Conversation =>
        c.id === conversationId ? { ...c, projectId: projectId ?? undefined } : c;
      return {
        conversations: state.conversations.map(patch),
        agentConversations: state.agentConversations.map(patch),
      };
    }),

  setConversationPinned: (conversationId, pinned) => {
    const before = get();
    const unpinnedId = pinned ? undefined : conversationId;
    set((state) => {
      // A chat pinned from More that the rail's page does not list joins the rail's list it belongs to, so Pinned
      // shows it now, as the server's next read would.
      const fromMore = pinned ? moreRowMissingFromRail(state, conversationId) : null;
      const updated = withPinned(
        fromMore?.list === 'conversations' ? [...state.conversations, fromMore.row] : state.conversations,
        conversationId,
        pinned,
      );
      const shared = withPinned(
        fromMore?.list === 'sharedConversations' ? [...state.sharedConversations, fromMore.row] : state.sharedConversations,
        conversationId,
        pinned,
      );
      const moreChats = patchMoreChatsRows(state.moreChats, (rows) => {
        const next = withPinned(rows, conversationId, pinned);
        return next === rows ? rows : inServerOrder(next);
      });
      if (updated === state.conversations && shared === state.sharedConversations && moreChats === state.moreChats) {
        return state;
      }
      return {
        conversations: updated === state.conversations ? state.conversations : orderPinnedFirst(updated, unpinnedId),
        sharedConversations: shared === state.sharedConversations ? state.sharedConversations : orderPinnedFirst(shared, unpinnedId),
        moreChats,
      };
    });
    const after = get();
    return () => {
      const now = get();
      // Lists that changed meanwhile (a refetch, another row's pin) keep their order; only this row's pin is put back.
      if (now.conversations !== after.conversations || now.sharedConversations !== after.sharedConversations || now.moreChats !== after.moreChats) {
        now.setConversationPinned(conversationId, !pinned);
        return;
      }
      set({ conversations: before.conversations, sharedConversations: before.sharedConversations, moreChats: before.moreChats });
    };
  },

  addPendingConversation: (slotId) =>
    set((state) => ({
      pendingConversations: {
        ...state.pendingConversations,
        [slotId]: {
          slotId,
          title: null,
          isGenerating: true,
          createdAt: Date.now(),
        },
      },
    })),

  updatePendingConversationTitle: (slotId, title) =>
    set((state) => {
      const existing = state.pendingConversations[slotId];
      if (!existing) return state;
      return {
        pendingConversations: {
          ...state.pendingConversations,
          [slotId]: { ...existing, title },
        },
      };
    }),

  resolvePendingConversation: (slotId, conversation, options) =>
    set((state) => {
      const { [slotId]: _removed, ...remaining } = state.pendingConversations;
      const slotForPending = state.slots[slotId];
      const isAgentStream =
        options?.isAgentStream ??
        Boolean(slotForPending?.threadAgentId);
      const isProjectScoped =
        Boolean(slotForPending?.projectId) || Boolean(conversation.projectId);

      const nextMain =
        isAgentStream || isProjectScoped
          ? state.conversations
          : [conversation, ...state.conversations.filter((c) => c.id !== conversation.id)];
      const nextAgent = isAgentStream
        ? [conversation, ...state.agentConversations.filter((c) => c.id !== conversation.id)]
        : state.agentConversations;
      const nextNewlyResolved = new Set(state.newlyResolvedIds);
      nextNewlyResolved.add(conversation.id);
      return {
        pendingConversations: remaining,
        conversations: nextMain,
        agentConversations: nextAgent,
        newlyResolvedIds: nextNewlyResolved,
        ...(isProjectScoped ? { projectsVersion: state.projectsVersion + 1 } : {}),
      };
    }),

  clearNewlyResolvedId: (conversationId) =>
    set((state) => {
      if (!state.newlyResolvedIds.has(conversationId)) return state;
      const next = new Set(state.newlyResolvedIds);
      next.delete(conversationId);
      return { newlyResolvedIds: next };
    }),

  clearPendingConversation: (slotId) =>
    set((state) => {
      const { [slotId]: _removed, ...remaining } = state.pendingConversations;
      return { pendingConversations: remaining };
    }),

  // ── URL sync ─────────────────────────────────────────────────────

  setHasConsumedUrlNavigation: (consumed) => set({ hasConsumedUrlNavigation: consumed }),



  // ── Settings actions ─────────────────────────────────────────────

  setPreviewFile: (file) => set({ previewFile: file }),

  setPreviewMode: (mode) => set({ previewMode: mode }),

  setExpansionViewMode: (mode) => set({ expansionViewMode: mode }),

  clearPreview: () =>
    set((state) => {
      // Revoke blob URL if present
      if (state.previewFile?.url?.startsWith('blob:')) {
        URL.revokeObjectURL(state.previewFile.url);
      }
      return { previewFile: null, previewMode: 'sidebar' };
    }),

  setMode: (mode) => set((state) => ({
    settings: { ...state.settings, mode },
  })),

  setQueryMode: (queryMode) =>
    set((state) => ({
      settings: {
        ...state.settings,
        queryMode,
        ...(queryMode === 'web-search' ? { filters: { apps: [], kb: [] } } : {}),
      },
    })),

  setAgentStrategy: (agentStrategy) => set((state) => ({
    settings: { ...state.settings, agentStrategy },
  })),

  setFilters: (filters) => set((state) => ({
    settings: { ...state.settings, filters },
  })),

  setSelectedModelForCtx: (ctxKey, model) => set((state) => ({
    settings: {
      ...state.settings,
      selectedModels: { ...state.settings.selectedModels, [ctxKey]: model },
    },
  })),

  setDefaultModelForCtx: (ctxKey, model) => set((state) => ({
    settings: {
      ...state.settings,
      defaultModels: { ...state.settings.defaultModels, [ctxKey]: model },
    },
  })),

  setAvailableModelsForCtx: (ctxKey, models) => set((state) => ({
    settings: {
      ...state.settings,
      availableModels: {
        ...state.settings.availableModels,
        [ctxKey]: { models, fetchedAt: Date.now() },
      },
    },
  })),

  // Not persisted: a pick kept across sessions outlived the reason for it and
  // hid the model's and the agent's defaults.
  setReasoningEffortForCtx: (ctxKey, effort) => set((state) => ({
    settings: {
      ...state.settings,
      reasoningEffort: { ...state.settings.reasoningEffort, [ctxKey]: effort },
    },
  })),

  setAgentDefaultReasoningEffort: (ctxKey: string, effort: import('./types').ReasoningEffort | null) => set((state) => ({
    settings: {
      ...state.settings,
      agentDefaultReasoningEffort: {
        ...state.settings.agentDefaultReasoningEffort,
        [ctxKey]: effort,
      },
    },
  })),

  setNewChatDefaultModel: (model) => set((state) => ({
    settings: { ...state.settings, newChatDefaultModel: model },
  })),

  setAgentCapabilities: (caps) => set((state) => {
    const next: AgentCapabilities = { ...state.settings.agentCapabilities, ...caps };
    lsSetAgentCapabilities(next);
    return { settings: { ...state.settings, agentCapabilities: next } };
  }),

  setScopedAgentCapabilities: (agentId, caps) => set((state) => {
    const current: AgentCapabilities = state.scopedAgentCapabilities[agentId]
      ?? lsGetAgentCapabilities(agentId);
    const next: AgentCapabilities = { ...current, ...caps };
    lsSetAgentCapabilities(next, agentId);
    return {
      scopedAgentCapabilities: { ...state.scopedAgentCapabilities, [agentId]: next },
    };
  }),

  // ── Search actions ──────────────────────────────────────────────

  setSearchResults: (results, searchId, query, responseMeta = null) =>
    set({
      searchResults: results,
      searchId,
      searchQuery: query,
      searchResponseMeta: (responseMeta as Record<string, unknown> | null) ?? null,
      searchError: null,
    }),

  setIsSearching: (loading) =>
    set((state) => ({
      isSearching: loading,
      searchResponseMeta: loading ? null : state.searchResponseMeta,
    })),

  setSearchError: (error) =>
    set((state) => ({
      searchError: error,
      searchResponseMeta: error ? null : state.searchResponseMeta,
    })),

  clearSearchResults: () =>
    set({
      searchResults: [],
      searchQuery: '',
      isSearching: false,
      searchId: null,
      searchError: null,
      searchResponseMeta: null,
    }),

  // ── Cache ────────────────────────────────────────────────────────

  setCollectionNamesCache: (cache) =>
    set((state) => ({
      collectionNamesCache: { ...state.collectionNamesCache, ...cache },
    })),

  // Field by field: writers know different things about a node (its place, its file type),
  // and one leaving a field out must not erase what another wrote.
  setCollectionMetaCache: (cache) =>
    set((state) => {
      const next = { ...state.collectionMetaCache };
      for (const [id, meta] of Object.entries(cache)) {
        const defined = Object.fromEntries(Object.entries(meta).filter(([, value]) => value !== undefined));
        next[id] = { ...next[id], ...defined } as CollectionMeta;
      }
      return { collectionMetaCache: next };
    }),

  // ── Reset ────────────────────────────────────────────────────────

  reset: () => {
    // Abort all in-flight streams before resetting
    const { slots } = get();
    for (const slot of Object.values(slots)) {
      slot.abortController?.abort();
    }
    unreadOwnerId = null;
    // The capability toggles are a device setting kept in localStorage; the
    // snapshot taken at module load may be older than what was chosen since.
    set({ ...initialState, settings: { ...initialState.settings, agentCapabilities: lsGetAgentCapabilities() } });
  },
}));

// Electron signs out without a page load: a stream or list left from the last
// user must not reach the next one's chats.
onSessionEnd(() => useChatStore.getState().reset());

/**
 * Sentinel context key for the non-agent (Assistant) chat. Using a Unicode
 * private-use sentinel avoids collision with any real agent id.
 */
export const ASSISTANT_CTX = '__assistant__';

/** Build the context key from an (effective) agent id or null. */
export const ctxKeyFromAgent = (agentId: string | null | undefined): string =>
  agentId && agentId.trim() ? agentId : ASSISTANT_CTX;

/**
 * Resolve the model that should be used for `ctxKey`: the user's selection
 * if present, else the context default. Reads the current store snapshot.
 */
export function getEffectiveModel(
  ctxKey: string,
): import('./types').ModelOverride | null {
  const state = useChatStore.getState();
  const { selectedModels, defaultModels, newChatDefaultModel } = state.settings;
  const selected = selectedModels[ctxKey];
  if (selected) return selected;
  // Only a new assistant chat opens on it; a project keeps its concrete model.
  if (ctxKey === ASSISTANT_CTX && newChatDefaultModel && !state.projectScope) {
    return newChatDefaultModel;
  }
  return defaultModels[ctxKey] ?? null;
}

/**
 * Whether `model` is flagged `isReasoning` in the model catalog fetched for
 * `ctxKey`. Used to gate sending an agent's default effort and the picker's
 * displayed default — sending an effort value for a non-reasoning model is
 * harmless server-side (the LLM factory ignores it), but omitting it keeps
 * outgoing payloads clean for models that don't support the concept at all.
 */
export function isModelReasoningCapable(
  ctxKey: string,
  model: import('./types').ModelOverride | null,
): boolean {
  if (!model) return false;
  const models = useChatStore.getState().settings.availableModels[ctxKey]?.models ?? [];
  return Boolean(
    models.find((m) => m.modelKey === model.modelKey && m.modelName === model.modelName)
      ?.isReasoning,
  );
}

/**
 * The model's own default reasoning effort for `model` in `ctxKey`'s list, or
 * `null` when none is stored.
 */
export function getModelDefaultReasoningEffort(
  ctxKey: string,
  model: import('./types').ModelOverride | null,
): import('./types').ReasoningEffort | null {
  if (!model) return null;
  const models = useChatStore.getState().settings.availableModels[ctxKey]?.models ?? [];
  const match = models.find((m) => m.modelKey === model.modelKey && m.modelName === model.modelName);
  return normalizeReasoningEffort(match?.defaultReasoningEffort ?? null);
}

/**
 * The agent-configured default reasoning effort for `ctxKey`, populated when
 * `fetchModelsForContext` loads an agent's config. Returns `null` when the
 * context is the universal assistant or the agent has no default configured.
 */
export function getAgentDefaultReasoningEffort(
  ctxKey: string,
): import('./types').ReasoningEffort | null {
  return useChatStore.getState().settings.agentDefaultReasoningEffort[ctxKey] ?? null;
}

/**
 * Effort to put on a chat request for `model` in `ctxKey`: the user's pick,
 * else the agent default for a reasoning model, else none (backend default).
 */
export function getRequestReasoningEffort(
  ctxKey: string,
  model: import('./types').ModelOverride | null,
): import('./types').ReasoningEffort | null {
  const catalog = useChatStore.getState().settings.availableModels[ctxKey]?.models;
  // Effort belongs to configured models; an edition entry sends none.
  if (
    model &&
    catalog &&
    !catalog.some((m) => m.modelKey === model.modelKey && m.modelName === model.modelName)
  ) {
    return null;
  }
  const picked = useChatStore.getState().settings.reasoningEffort[ctxKey] ?? null;
  return picked ?? (isModelReasoningCapable(ctxKey, model) ? getAgentDefaultReasoningEffort(ctxKey) : null);
}

/**
 * Union of the universal agent mode's toolset + MCP catalogs. The two load independently
 * (toolsets are paginated via my-toolsets, MCP servers load in one shot via my-mcp-servers)
 * so they're kept as separate store fields — this is the single source of truth for "every
 * tool fullName currently selectable", used by `toggleTool`/`setGroupToolsEnabled` in
 * `universal-agent-resources-panel.tsx` to diff the explicit selection against "everything".
 */
export function getUniversalCombinedCatalog(): string[] {
  const state = useChatStore.getState();
  return [...state.universalAgentToolCatalogFullNames, ...state.universalAgentMcpCatalogFullNames];
}

// ── Store-write diff subscriber (debug only) ────────────────────
// Logs which top-level fields changed per set() call. This lets us
// correlate store writes with component re-renders in the debug output.
if (typeof window !== 'undefined') {
  // debugLog imported at top of file
  const trackedFields = [
    'slots', 'activeSlotId', 'conversations', 'sharedConversations',
    'isConversationsLoading', 'conversationsError', 'pagination',
    'pendingConversations', 'isMoreChatsPanelOpen', 'moreChatsSectionType', 'moreChats',
    'agentSidebarAgentId', 'agentConversations', 'agentConversationsPagination',
    'isAgentConversationsLoading', 'agentConversationsError', 'isAgentMoreChatsPanelOpen', 'agentMoreChatsPagination',
    'agentStreamTools',
    'agentToolCatalogFullNames',
    'agentChatConnectors',
    'agentChatKbIds',
    'agentKnowledgeCollectionRows',
    'agentChatToolGroups',
    'agentChatMcpGroups',
    'agentKnowledgeDefaults',
    'agentKnowledgeScope',
    'agentContextDisplayName',
    'agentContextAccess',
    'projectScope',
    'projectKnowledgeScope',
    'projectStreamTools',
    'universalAgentStreamTools',
    'universalAgentToolCatalogFullNames',
    'universalAgentToolGroups',
    'universalAgentToolsLoading',
    'universalAgentToolsError',
    'universalAgentMcpCatalogFullNames',
    'universalAgentMcpGroups',
    'universalAgentMcpLoading',
    'universalAgentMcpError',
    'settings', 'previewFile', 'previewMode', 'expansionViewMode',
    'scopedAgentCapabilities',
    'collectionNamesCache', 'collectionMetaCache', 'conversationsVersion',
    'searchResults', 'searchQuery', 'searchId', 'isSearching', 'searchError',
  ] as const;

  useChatStore.subscribe((state, prev) => {
    const changed: string[] = [];
    for (const field of trackedFields) {
      if (!Object.is((state as unknown as Record<string, unknown>)[field], (prev as unknown as Record<string, unknown>)[field])) {
        changed.push(field);
      }
    }
    if (changed.length > 0) {
      debugLog.storeWrite(changed);
    }
  });
}
