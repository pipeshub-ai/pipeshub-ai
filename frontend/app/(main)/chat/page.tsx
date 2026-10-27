'use client';

import React, { useEffect, useCallback, useLayoutEffect, useRef, useMemo, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useAui } from '@assistant-ui/react';
import { DemoSuggestions, MessageList, ChatInputWrapper, SearchResultsView } from './components';
import { useDemoDataActive, useDemoDataStatus } from '@/app/(main)/workspace/connectors/demo-data/use-demo-data';
import { DemoDataRemovalNotice } from '@/app/(main)/workspace/connectors/demo-data/components';
import { AgentChatHeader } from '@/config';
import { getAgentSidebarRowMenuAccess } from './sidebar/agent-sidebar-row-access';
import { ConversationHeaderOverlay } from '@/config';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
import {
  applyConversationModelInfoToStore,
  findModelInfoInConversationLists,
  pickModelInfoFromConversationBundle,
} from '@/chat/utils/apply-conversation-model-info';
import { ChatSuggestion, type AppliedFilters } from '@/chat/types';
import { appliedFiltersFromNodes, restoreAppliedFilters } from '@/chat/utils/restore-applied-filters';
import { ChatApi } from '@/chat/api';
import { buildChatHref } from '@/chat/build-chat-url';
import {
  AgentsApi,
  buildAgentChatToolGroups,
  buildAgentChatMcpGroups,
  extractAgentKnowledgeDefaults,
  extractAgentKnowledgeConnectors,
  extractAgentKnowledgeCollectionRows,
} from '@/app/(main)/agents/api';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { buildExternalStoreConfig, loadHistoricalMessages } from '@/chat/runtime';
import { debugLog } from '@/chat/debug-logger';
import { useCommandStore } from '@/lib/store/command-store';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { SidebarExpandButton } from '@/app/components/sidebar/sidebar-expand-button';
import { useIsMobile, useIsBelowDesktop } from '@/lib/hooks/use-is-mobile';
import { Flex, Box, Text, Avatar, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { FilePreviewInlinePanel, FilePreviewFullscreen } from '@/app/components/file-preview';
import { FilePreviewMobile } from '@/app/components/file-preview/file-preview-mobile';
import { ShareSidebar, ShareHeaderGroup } from '@/app/components/share';
import type { SharedAvatarMember } from '@/app/components/share';
import { createChatShareAdapter } from './share-adapter';
import { ChatSearch } from './components/search';
import { isCommandKey } from '@/lib/utils/platform';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useGitHubStars } from '@/app/components/workspace-menu/hooks/use-github-stars';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore, selectIsAdmin } from '@/lib/store/user-store';
import { toast } from '@/lib/store/toast-store';
import { isProcessedError } from '@/lib/api/api-error';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { useBrand } from '@/lib/branding';
import { chatContentColumnStyle, clampSplitWidth } from './constants';
import { useSplitWidth } from '@/chat/hooks/use-split-width';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import {
  useFeatureFlagsStore,
  selectProjectsEnabled,
  selectApiToolsetsEnabled,
} from '@/lib/store/feature-flags-store';
import { ApiToolsetsApi } from '@/app/(main)/workspace/api-toolsets/api';
import { buildApiActionGroups } from '@/chat/tool-groups';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectDetail } from '@/chat/project-types';
import { useProjectScopeHydration } from '@/chat/hooks/use-project-scope-hydration';
import { useRemoteRunPoll } from '@/chat/hooks/use-remote-run-poll';
import { useHydrateUnreadConversations } from '@/chat/hooks/use-hydrate-unread-conversations';

const footerLinkStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'var(--space-1)',
  opacity: 0.7,
  textDecoration: 'none',
  color: 'inherit',
};

function ChatFooterLinks() {
  const { t } = useTranslation();
  const stars = useGitHubStars();
  const brand = useBrand();
  const githubUrl = brand.githubUrl;
  const docsUrl = brand.docsHref('introduction');

  if (!githubUrl && !docsUrl) return null;

  return (
    <Flex
      align="center"
      justify="center"
      gap="3"
      style={{ marginTop: 'var(--space-1)', paddingBottom: 0 }}
    >
      {githubUrl ? (
      <a
        href={githubUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="touch-hit"
        style={footerLinkStyle}
        onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
        onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.7'; }}
      >
        <img
          src="/icons/logos/github-logo.svg"
          width={14}
          height={14}
          alt=""
          style={{ flexShrink: 0 }}
        />
        <span style={{ fontSize: 12, color: 'var(--olive-9)', whiteSpace: 'nowrap' }}>
          GitHub
        </span>
        {stars && (
          <>
            <Text style={{ color: 'var(--olive-6)', fontSize: 12 }}>·</Text>
            <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--slate-10)', whiteSpace: 'nowrap' }}>
              {stars}
              <MaterialIcon
                name="star"
                size={11}
                color="var(--slate-10)"
                style={{ marginLeft: 1, verticalAlign: 'middle' }}
              />
            </span>
          </>
        )}
      </a>
      ) : null}

      {githubUrl && docsUrl ? (
      <Text style={{ color: 'var(--olive-6)', fontSize: 12 }}>·</Text>
      ) : null}

      {docsUrl ? (
      <a
        href={docsUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="touch-hit"
        style={footerLinkStyle}
        onMouseEnter={(e) => { e.currentTarget.style.opacity = '1'; }}
        onMouseLeave={(e) => { e.currentTarget.style.opacity = '0.7'; }}
      >
        <img
          src="/icons/common/reader.svg"
          width={14}
          height={14}
          alt=""
          style={{ flexShrink: 0 }}
        />
        <span style={{ fontSize: 12, color: 'var(--olive-9)', whiteSpace: 'nowrap' }}>
          {t('common.docs')}
        </span>
      </a>
      ) : null}
    </Flex>
  );
}

/**
 * Inner content component that uses assistant-ui hooks.
 * Must be inside AssistantRuntimeProvider.
 *
 * Responsibilities:
 * - URL ↔ store sync (bi-directional)
 * - Slot lifecycle (create, init, evict)
 * - Command registration (newChat)
 * - Renders new-chat view or MessageList
 */

// ── Split-pane constants (module scope — never re-declared on re-render) ────
const CHAT_PANEL_LS_KEY = 'chat-split-panel-width-px';
const CHAT_PANEL_MIN_PX = 280;
const CHAT_PANEL_DEFAULT_PX = 420;

function ChatContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { t } = useTranslation();
  const conversationId = searchParams.get('conversationId');
  const rawAgentParam = searchParams.get('agentId');
  const agentId = rawAgentParam?.trim() ? rawAgentParam : null;
  const projectsEnabled = useFeatureFlagsStore(selectProjectsEnabled);
  const apiActionsEnabled = useFeatureFlagsStore(selectApiToolsetsEnabled);
  // A thread can't be scoped to both an agent and a project — agentId wins.
  // When the feature flag is off, projectId is always null so the UI degrades
  // to a normal chat without project context.
  const rawProjectParam = searchParams.get('projectId');
  const projectId = !agentId && projectsEnabled && rawProjectParam?.trim() ? rawProjectParam : null;

  const aui = useAui();
  useHydrateUnreadConversations();

  // ── Narrow selectors: only re-render when the selected value changes ──
  // Actions are stable refs in Zustand — selecting them individually
  // prevents this component from re-rendering on background slot updates.
  const previewFile = useChatStore((s) => s.previewFile);
  const previewMode = useChatStore((s) => s.previewMode);
  const setPreviewMode = useChatStore((s) => s.setPreviewMode);
  const clearPreview = useChatStore((s) => s.clearPreview);

  // Nav sidebar collapse state — used to show the expand button when collapsed
  const isNavCollapsed = useSidebarWidthStore((s) => s.isNavCollapsed);

  // Slot-scoped state for rendering decisions.
  // CRITICAL: select individual PRIMITIVE fields — never select the full
  // slot object. `updateSlot(slotId, { streamingContent })` creates a new
  // slot reference on every rAF flush; selecting the object would re-render
  // this component ~60×/sec during streaming.
  const activeSlotId = useChatStore((s) => s.activeSlotId);
  const hasActiveSlot = useChatStore((s) =>
    s.activeSlotId ? !!s.slots[s.activeSlotId] : false
  );
  const activeSlotIsTemp = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isTemp ?? false : false
  );
  const activeSlotIsInitialized = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isInitialized ?? false : false
  );
  const activeSlotIsStreaming = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isStreaming ?? false : false
  );
  const activeSlotConvId = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.convId ?? null : null
  );
  const activeSlotRemoteRunInProgress = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.remoteRunInProgress ?? false : false
  );
  const activeSlotMsgCount = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.messages.length ?? 0 : 0
  );
  const activeSlotThreadAgentId = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.threadAgentId ?? null : null
  );
  const activeSlotIsOwner = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isOwner ?? null : null
  );
  const activeSlotConversationModelInfo = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.conversationModelInfo : undefined
  );
  /** Prefer slot scope so history/share stay correct if URL query is missing agentId. */
  const historyAndShareAgentId =
    (activeSlotThreadAgentId?.trim() || null) ?? agentId;

  // ── Render-reason tracking ──────────────────────────────────────
  debugLog.tick('[chat] [ChatContent]');
  const prevChatContentRef = useRef<Record<string, unknown>>({});
  const currentChatContentVals: Record<string, unknown> = {
    conversationId, agentId, previewFile, previewMode,
    activeSlotId, hasActiveSlot, activeSlotIsTemp,
    activeSlotIsInitialized, activeSlotIsStreaming, activeSlotConvId,
    activeSlotMsgCount,
  };
  const chatContentReasons: string[] = [];
  for (const [k, v] of Object.entries(currentChatContentVals)) {
    if (!Object.is(v, prevChatContentRef.current[k])) chatContentReasons.push(k);
  }
  if (chatContentReasons.length > 0) {
    debugLog.reason('[chat] [ChatContent]', chatContentReasons);
  }
  prevChatContentRef.current = currentChatContentVals;

  // ── Command palette state ──
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);

  // Register the 'newChat' command so any trigger (CMD+N, buttons) works
  useEffect(() => {
    const { register, unregister } = useCommandStore.getState();
    register('newChat', () => {
      useNotificationStore.getState().closePanel();
      const store = useChatStore.getState();

      // 0. Reset search mode if active (URL won't change since both are /chat)
      if (store.settings.mode === 'search') {
        store.setMode('chat');
        store.clearSearchResults();
      }

      const urlParams =
        typeof window !== 'undefined'
          ? new URLSearchParams(window.location.search)
          : null;
      const rawAgentInUrl = urlParams?.get('agentId');
      const agentIdInUrl = rawAgentInUrl?.trim() ? rawAgentInUrl : null;
      const rawProjectInUrl = urlParams?.get('projectId');
      const projectIdInUrl = !agentIdInUrl && rawProjectInUrl?.trim() ? rawProjectInUrl : null;

      // 1. Detach visible thread only — background streams keep running (parallel chats)
      store.clearActiveSlot();

      // 2–3. Sync URL: stay on agent/project new-chat when scoped, else main home
      if (agentIdInUrl) {
        const href = buildChatHref({ agentId: agentIdInUrl });
        window.history.replaceState(null, '', href);
        router.replace(href);
      } else if (projectIdInUrl) {
        const href = buildChatHref({ projectId: projectIdInUrl });
        window.history.replaceState(null, '', href);
        router.replace(href);
      } else {
        const href = '/chat/';
        window.history.replaceState(null, '', href);
        router.replace(href);
      }
    });

    // Register 'openCommandPalette' command for sidebar / external triggers
    register('openCommandPalette', () => {
      setIsCommandPaletteOpen(true);
    });

    // Global keyboard shortcuts
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!isCommandKey(e)) return;

      // ⌘+Shift+K → New Chat
      if (e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        // Close command palette if open, then create new chat
        setIsCommandPaletteOpen(false);
        useCommandStore.getState().dispatch('newChat');
        return;
      }

      // ⌘+K → Open/close command palette
      if (!e.shiftKey && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setIsCommandPaletteOpen((prev) => !prev);
        return;
      }

      // ⌘+N → New Chat
      if (e.key === 'n') {
        e.preventDefault();
        useCommandStore.getState().dispatch('newChat');
      }
    };
    window.addEventListener('keydown', handleKeyDown);

    return () => {
      unregister('newChat');
      unregister('openCommandPalette');
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [router]);

  // Populate agent side-effects (tools, display name) and kick off the model
  // fetch for the current context. The shared `fetchModelsForContext` handles
  // caching, default resolution, and stale-selection invalidation per ctxKey.
  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      const store = useChatStore.getState();
      const ctxKey = ctxKeyFromAgent(agentId);

      if (agentId?.trim()) {
        try {
          // Opening an existing chat: a chat shared with the user may belong to an agent they cannot
          // open, and the chat itself loads without it, so that is not worth an error toast.
          const { agent, toolFullNames } = await AgentsApi.getAgent(agentId, { quiet: Boolean(conversationId) });
          if (cancelled) return;
          const knowledgeDefaults = extractAgentKnowledgeDefaults(agent);
          const collectionRows = extractAgentKnowledgeCollectionRows(agent);
          const wholeCollections = collectionRows.filter((r) => !r.nodeType);
          const kbIds =
            wholeCollections.length > 0
              ? wholeCollections.map((r) => r.id)
              : knowledgeDefaults.kb;
          const knowledgeDefaultsForStore = {
            ...knowledgeDefaults,
            kb: kbIds,
          };
          const connectors = extractAgentKnowledgeConnectors(agent);
          const attachedApiTools = apiActionsEnabled ? (agent?.apiToolsetTools ?? []) : [];
          // Without the list there is nothing to name the operations by: treat it as no API actions.
          const apiActionGroups = attachedApiTools.length
            ? buildApiActionGroups(await ApiToolsetsApi.listInstances().catch(() => []), attachedApiTools)
            : [];
          if (cancelled) return;
          const toolGroups = [...buildAgentChatToolGroups(agent), ...apiActionGroups];
          const mcpGroups = buildAgentChatMcpGroups(agent);
          const deprecatedToolNames = (agent?.toolsets ?? [])
            .flatMap((ts) => ts.tools ?? [])
            .filter((tool) => tool.deprecated === true)
            .map((tool) => tool.name);
          store.hydrateAgentChatResources({
            toolCatalogFullNames: [...toolFullNames, ...apiActionGroups.flatMap((g) => g.fullNames)],
            toolGroups,
            mcpGroups,
            connectors,
            kbIds,
            knowledgeCollectionRows: collectionRows,
            knowledgeDefaults: knowledgeDefaultsForStore,
            deprecatedToolNames,
            hasWebSearch: agent?.webSearch != null,
          });
          store.setAgentContextDisplayName(agent?.name?.trim() || null);
          store.setAgentContextCreatedBy(agent?.createdBy ?? null);
          store.setAgentContextAccess(
            agent ? getAgentSidebarRowMenuAccess(agent) : null,
          );

          // Warn when any tool attached to this agent has been removed from
          // server code since the agent was last saved (deprecated=true is
          // stamped by the GET /agent/:id handler at read time).
          if (deprecatedToolNames.length > 0) {
            toast.error(t('chat.toasts.deprecatedTools'), {
              action: {
                label: t('chat.toasts.openAgentBuilder'),
                onClick: () =>
                  router.push(`/agents/edit?agentKey=${encodeURIComponent(agentId!)}`),
              },
            });
          }

          // hydrateAgentChatResources always resets agentKnowledgeScope to null.
          // On page reload with an existing conversationId, loadHistory may have
          // already set the scope from the last message's appliedFilters before
          // getAgent resolved. Re-apply it so the race doesn't wipe it out.
          if (conversationId) {
            const freshStore = useChatStore.getState();
            const existing = freshStore.getSlotByConvId(conversationId, { forAgentId: agentId });
            if (existing?.slot.isInitialized) {
              const lastWithFilters = [...existing.slot.messages].reverse().find(
                (msg) =>
                  msg.role === 'user' &&
                  (msg.metadata as { custom?: { appliedFilters?: import('./types').AppliedFilters } })
                    ?.custom?.appliedFilters != null
              );
              const af = (
                lastWithFilters?.metadata as {
                  custom?: { appliedFilters?: import('./types').AppliedFilters };
                }
              )?.custom?.appliedFilters;
              // Every bucket, not only apps and collections: a folder or record
              // the agent chat was narrowed to stays in its scope.
              if (af) restoreAppliedFilters(af, true);
            }
          }
        } catch (error) {
          if (!cancelled) {
            console.error('Failed to fetch agent details:', error);
            store.hydrateAgentChatResources(null);
            store.setAgentContextDisplayName(null);
            store.setAgentContextCreatedBy(null);
            store.setAgentContextAccess(null);
          }
        }
      } else {
        store.hydrateAgentChatResources(null);
        store.setAgentContextDisplayName(null);
        store.setAgentContextCreatedBy(null);
        store.setAgentContextAccess(null);
      }

      try {
        // Force a refetch for agent contexts: the agent's configured models
        // can change between visits (Agent Builder save, admin edits) and
        // stale cached lists would surface wrong defaults in the pill and
        // the model selector. Assistant (org-wide) models change far less
        // often, so the normal freshness window is fine there.
        const force = Boolean(agentId?.trim());
        await fetchModelsForContext(ctxKey, { force });
      } catch (error) {
        if (!cancelled && useServicesHealthStore.getState().apiServerReachable) {
          console.error('Failed to fetch models for context', ctxKey, error);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [agentId, apiActionsEnabled, router, t]);

  // Keep the store's `activeProjectId` (read by ProjectsSection /
  // ProjectConversationsSidebar to highlight the open project) in sync with the URL.
  useEffect(() => {
    useChatStore.getState().setActiveProjectId(projectId);
  }, [projectId]);

  // ── URL → Store sync ──────────────────────────────────────────────
  // When URL changes (sidebar click, browser back), create/reuse a slot.
  // useRef flag prevents the store→URL effect from bouncing back.
  const urlSyncingRef = useRef(false);

  useEffect(() => {
    urlSyncingRef.current = true;
    const store = useChatStore.getState();

    // Exit search mode on any navigation (sidebar click, new chat, etc.)
    if (store.settings.mode === 'search') {
      store.setMode('chat');
      store.clearSearchResults();
    }

    if (!conversationId) {
      const activeSlot = store.activeSlotId ? store.slots[store.activeSlotId] : null;
      if (agentId) {
        const keepInFlight =
          !!activeSlot &&
          (activeSlot.isStreaming || activeSlot.stopping) &&
          activeSlot.threadAgentId === agentId;
        if (store.activeSlotId && !keepInFlight) {
          debugLog.flush('chat-switch', { from: store.activeSlotId, to: null, reason: 'agent-new-chat-url' });
          store.clearActiveSlot();
        }
      } else if (store.activeSlotId && activeSlot?.threadAgentId) {
        debugLog.flush('chat-switch', { from: store.activeSlotId, to: null, reason: 'leave-agent-for-main-home' });
        useChatStore.setState({ activeSlotId: null });
        // The main assistant starts unscoped, not with what was picked before the agent.
        store.setFilters({ apps: [], kb: [] });
        store.bumpConversationsVersion();
      } else if (
        store.activeSlotId &&
        activeSlot?.isTemp &&
        activeSlot.projectId !== projectId
      ) {
        // Switched project workspace (or left it) while a draft new-chat slot
        // for a *different* project scope was active — don't leak it here.
        debugLog.flush('chat-switch', { from: store.activeSlotId, to: null, reason: 'switch-project-scope' });
        store.clearActiveSlot();
      } else if (store.activeSlotId && (!activeSlot || !activeSlot.isTemp)) {
        debugLog.flush('chat-switch', { from: store.activeSlotId, to: null });
        useChatStore.setState({ activeSlotId: null });
      }
    } else {
      const urlAgentId = agentId;
      const existing = store.getSlotByConvId(conversationId, { forAgentId: urlAgentId });
      if (existing) {
        const toolsPatch = urlAgentId
          ? {
              agentStreamTools:
                store.agentStreamTools === null ? null : [...store.agentStreamTools],
            }
          : {};
        store.updateSlot(existing.slotId, {
          threadAgentId: urlAgentId || null,
          ...toolsPatch,
        });
        if (store.activeSlotId !== existing.slotId) {
          debugLog.flush('chat-switch', { from: store.activeSlotId, to: existing.slotId, convId: conversationId });
          store.setActiveSlot(existing.slotId);
        }

        // For already-initialized slots loadHistory won't run again, so restore
        // filters directly from the messages cached in the slot. Walk backwards
        // to find the last user message that carried appliedFilters.
        const cachedSlot = store.slots[existing.slotId];
        if (cachedSlot?.isInitialized) {
          const lastWithFilters = [...cachedSlot.messages]
            .reverse()
            .find(
              (msg) =>
                msg.role === 'user' &&
                (msg.metadata as { custom?: { appliedFilters?: AppliedFilters } } | undefined)?.custom
                  ?.appliedFilters != null
            );
          const af = (lastWithFilters?.metadata as { custom?: { appliedFilters?: AppliedFilters } } | undefined)
            ?.custom?.appliedFilters;
          if (af) {
            restoreAppliedFilters(af, Boolean(urlAgentId), projectId);
          } else {
            if (urlAgentId) {
              store.setAgentKnowledgeScope(null);
            } else {
              store.setFilters({ apps: [], kb: [] });
              if (projectId) store.setProjectKnowledgeScope(null);
            }
          }
        } else {
          // Slot not yet initialized — history will restore filters when it
          // loads, but clear stale filters from the previous conversation now
          // so the badge doesn't flash incorrectly.
          if (urlAgentId) {
            store.setAgentKnowledgeScope(null);
          } else {
            store.setFilters({ apps: [], kb: [] });
          }
        }
      } else {
        if (urlAgentId) {
          store.setAgentKnowledgeScope(null);
        } else {
          store.setFilters({ apps: [], kb: [] });
        }
        const newSlotId = store.createSlot(conversationId);
        if (urlAgentId) {
          store.updateSlot(newSlotId, {
            threadAgentId: urlAgentId,
            agentStreamTools:
              store.agentStreamTools === null ? null : [...store.agentStreamTools],
          });
        }
        debugLog.flush('chat-switch', { from: store.activeSlotId, to: newSlotId, convId: conversationId, newSlot: true });
        store.setActiveSlot(newSlotId);
      }
    }

    // Pre-fill per-thread model from sidebar/list rows (GET /conversations) before history GET finishes
    if (conversationId) {
      const st = useChatStore.getState();
      const targetSid = st.activeSlotId;
      if (targetSid) {
        const fromList = findModelInfoInConversationLists(st, conversationId, agentId);
        if (fromList) {
          st.updateSlot(targetSid, { conversationModelInfo: fromList });
        }
      }
    }

    // Allow store→URL sync again after a tick
    requestAnimationFrame(() => {
      urlSyncingRef.current = false;
    });
  }, [conversationId, agentId, projectId]);

  // ── Store → URL sync ──────────────────────────────────────────────
  // When streaming completes and assigns a convId to a temp slot, update URL.
  // Uses window.history.replaceState so the URL bar updates without a full
  // navigation. NOTE: Next.js 15 intercepts replaceState — useSearchParams
  // DOES update, which can re-trigger the URL→Store effect above. The
  // urlSyncingRef flag plus the newChat handler's URL cleanup prevent
  // infinite loops and stale re-activation. On page reload the
  // conversationId will be picked up from the URL as expected.
  useEffect(() => {
    const unsubscribe = useChatStore.subscribe((state, prev) => {
      if (urlSyncingRef.current) return;

      const slotId = state.activeSlotId;
      if (!slotId) return;

      const slot = state.slots[slotId];
      const prevSlot = prev.activeSlotId === slotId ? prev.slots[slotId] : null;

      // If convId changed from null to a real value, update URL
      if (slot?.convId && (!prevSlot || prevSlot.convId !== slot.convId)) {
        const loc = new URLSearchParams(window.location.search);
        const rawAid = slot.threadAgentId ?? loc.get('agentId');
        const aid = rawAid?.trim() ? rawAid : null;
        // A thread can't be scoped to both an agent and a project — agentId wins,
        // mirroring buildChatHref()'s precedence.
        const rawPid = aid ? null : (slot.projectId ?? loc.get('projectId'));
        const pid = rawPid?.trim() ? rawPid : null;
        const q = new URLSearchParams();
        if (aid) q.set('agentId', aid);
        else if (pid) q.set('projectId', pid);
        q.set('conversationId', slot.convId);
        window.history.replaceState(null, '', `/chat/?${q.toString()}`);
      }
    });
    return unsubscribe;
  }, []);

  // ── Slot initialization (load history for non-temp slots) ─────────
  useEffect(() => {
    if (!activeSlotId || !hasActiveSlot) return;
    if (activeSlotIsInitialized) return; // already loaded or new chat
    if (activeSlotIsTemp) return; // temp slots don't have history

    const convId = activeSlotConvId;
    if (!convId) return;

    let cancelled = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    const generationOf = () => useChatStore.getState().slots[activeSlotId]?.refreshGeneration ?? 0;
    // A notification can invalidate the conversation while this request is in flight; a response
    // fetched before that is stale. Refetch at once a few times; past that, back off and start over,
    // so a stale response (or error) never marks the slot initialized.
    const MAX_REFETCHES = 3;
    const RETRY_DELAY_MS = 500;

    const retryStale = (attempt: number): Promise<void> | undefined => {
      if (attempt < MAX_REFETCHES) return loadHistory(attempt + 1);
      retryTimer = setTimeout(() => {
        if (!cancelled) void loadHistory(0);
      }, RETRY_DELAY_MS);
      return undefined;
    };

    const loadHistory = async (attempt = 0): Promise<void> => {
      const generation = generationOf();
      try {
        const detail = historyAndShareAgentId
          ? await AgentsApi.fetchAgentConversation(historyAndShareAgentId, convId)
          : await ChatApi.fetchConversation(convId);
        if (cancelled) return;
        if (generationOf() !== generation) {
          return retryStale(attempt);
        }

        const messages = detail.messages;
        const isOwner = detail.conversation.access?.isOwner ?? false;
        const apiPagination = detail.pagination;
        const modelInfo = pickModelInfoFromConversationBundle({
          modelInfo: detail.conversation.modelInfo,
          messages: detail.messages,
        });

        // Always reset filters so pills from a previously viewed conversation
        // don't persist if this conversation carries no filter history.
        useChatStore.getState().setFilters({ apps: [], kb: [] });
        if (projectId) useChatStore.getState().setProjectKnowledgeScope(null);

        // Restore filter state from the most recent user message that carried filters.
        // This brings back the selected connectors/collections after a hard refresh.
        const lastFiltered = [...messages]
          .reverse()
          .find((msg) => msg.messageType === 'user_query' && msg.appliedFilters);

        if (lastFiltered?.appliedFilters) {
          restoreAppliedFilters(lastFiltered.appliedFilters, Boolean(historyAndShareAgentId), projectId);
        }

        const { messages: formattedMessages, unansweredAskUserQuestion } = loadHistoricalMessages(messages);
        useChatStore.getState().updateSlot(activeSlotId, {
          messages: formattedMessages,
          rawMessages: messages,
          isInitialized: true,
          hasLoaded: true,
          remoteRunInProgress: detail.conversation.status === 'Inprogress',
          isOwner,
          messagePagination: {
            currentPage: apiPagination.page,
            hasOlderMessages: apiPagination.hasNextPage,
            isLoadingOlder: false,
          },
          ...(modelInfo ? { conversationModelInfo: modelInfo } : {}),
          pendingAskUserQuestion: unansweredAskUserQuestion,
        });
      } catch (error) {
        if (!cancelled && generationOf() !== generation) {
          return retryStale(attempt);
        }
        console.error('Failed to load conversation history:', error);
        if (!cancelled) {
          // Mark as initialized to avoid infinite retries, but leave isOwner
          // untouched: a transient fetch failure shouldn't flip the share
          // button off for an actual owner.
          useChatStore.getState().updateSlot(activeSlotId, {
            isInitialized: true,
          });
          // The API client already explains HTTP failures in its own toast.
          if (!isProcessedError(error) && useServicesHealthStore.getState().apiServerReachable) {
            toast.error(t('chat.toasts.loadConversationFailed'));
          }
        }
      }
    };

    loadHistory();

    return () => {
      cancelled = true;
      if (retryTimer !== undefined) clearTimeout(retryTimer);
    };
  }, [activeSlotId, hasActiveSlot, activeSlotIsInitialized, activeSlotIsTemp, activeSlotConvId, historyAndShareAgentId, projectId, t]);

  // A run that outlived a refresh finishes on the server; reload once it has.
  useRemoteRunPoll(
    activeSlotId,
    activeSlotConvId,
    historyAndShareAgentId,
    activeSlotRemoteRunInProgress && activeSlotIsInitialized && !activeSlotIsStreaming,
  );

  // When sidebar/list rows arrive after the URL+slot are ready, backfill
  // `modelInfo` from GET /conversations (before history fetch completes)
  const conversations = useChatStore((s) => s.conversations);
  const sharedConversations = useChatStore((s) => s.sharedConversations);
  const agentConversations = useChatStore((s) => s.agentConversations);
  useEffect(() => {
    if (!conversationId || !activeSlotId) return;
    const store = useChatStore.getState();
    const slot = store.slots[activeSlotId];
    if (!slot || slot.convId !== conversationId) return;
    if (slot.conversationModelInfo) return;
    const fromList = findModelInfoInConversationLists(store, conversationId, historyAndShareAgentId);
    if (fromList) {
      store.updateSlot(activeSlotId, { conversationModelInfo: fromList });
    }
  }, [
    conversations,
    sharedConversations,
    agentConversations,
    conversationId,
    activeSlotId,
    historyAndShareAgentId,
  ]);

  // Restore model + mode for the active thread (per-slot `conversationModelInfo`)
  const modelCtxKey = useMemo(
    () => ctxKeyFromAgent(historyAndShareAgentId),
    [historyAndShareAgentId]
  );
  const prevSlotStreamingRef = useRef(false);
  useEffect(() => {
    if (!activeSlotId || !activeSlotConversationModelInfo) return;
    // Skip mode restoration when modelInfo was set by a stream that just completed —
    // the user's chosen mode should persist across turns in the same session.
    const wasStreaming = prevSlotStreamingRef.current;
    prevSlotStreamingRef.current = activeSlotIsStreaming;
    if (wasStreaming || activeSlotIsStreaming) return;
    applyConversationModelInfoToStore(activeSlotConversationModelInfo, modelCtxKey);
  }, [activeSlotId, activeSlotConversationModelInfo, modelCtxKey, activeSlotIsStreaming]);

  // Handle suggestion click - send message through runtime
  const handleSuggestionClick = (suggestion: ChatSuggestion) => {
    // If no slot exists yet (new chat), create one first
    if (!activeSlotId) {
      const store = useChatStore.getState();
      const newSlotId = store.createSlot(null);
      store.setActiveSlot(newSlotId);
      if (agentId) {
        store.updateSlot(newSlotId, {
          threadAgentId: agentId,
          agentStreamTools:
            store.agentStreamTools === null ? null : [...store.agentStreamTools],
        });
      } else if (projectId) {
        store.updateSlot(newSlotId, { projectId });
      }
    }

    aui.thread().append({
      role: 'user',
      content: [{ type: 'text', text: suggestion.text }],
      startRun: true,
    });
  };

  // ── Project new-chat resting state ──────────────────────────────────
  // `/chat/?projectId=…` with no conversationId is the project's new-chat
  // view. The composer lives in `ChatInputWrapper` (same as normal chat)
  // and sends in-place — no navigation, no page transition. The project
  // settings workspace (Instructions, Files, Tools, Members) is at
  // `/projects?projectId=…` and linked from the sidebar.
  //
  // The loaded detail also drives the composer's allow-list (connectors,
  // collections, tools) via `useProjectScopeHydration`. `projectsVersion`
  // bumps whenever project settings are saved anywhere in the app, so the
  // composer picks up changes without a reload.
  const projectsVersion = useChatStore((s) => s.projectsVersion);
  const [projectDetail, setProjectDetail] = useState<ProjectDetail | null>(null);
  useEffect(() => {
    if (!projectId) { setProjectDetail(null); return; }
    let cancelled = false;
    // The page shows the one error; the client's own toast made it two.
    ProjectApi.get(projectId, { suppressErrorToast: true }).then((p) => {
      if (!cancelled) setProjectDetail(p);
    }).catch((error: unknown) => {
      if (cancelled) return;
      setProjectDetail(null);
      toast.error(t('chat.projects.workspace.failedToLoad'));
      // A project the user can't open left them a working composer inside a
      // nameless project. Fall back to a plain chat (keeping an open thread).
      const status = isProcessedError(error) ? error.statusCode : undefined;
      if (status === 403 || status === 404) {
        router.replace(buildChatHref({ conversationId }));
      }
    });
    return () => { cancelled = true; };
  }, [projectId, projectsVersion, t, router, conversationId]);
  useProjectScopeHydration(projectDetail);
  const projectName = projectDetail?.name ?? null;
  const projectColor = projectDetail?.color ?? null;

  // ── Consume pending chat context from widget ──────────────────────
  const pendingConsumedRef = useRef(false);
  useEffect(() => {
    if (conversationId || pendingConsumedRef.current) return;

    const pending = usePendingChatStore.getState().consumePending();
    if (!pending) return;
    pendingConsumedRef.current = true;

    const store = useChatStore.getState();

    // A fresh slot unless the active one is an idle, empty new chat: reusing a
    // running or finished thread would show its Stop button and append there.
    let slotId = store.activeSlotId;
    const activeSlot = slotId ? store.slots[slotId] : undefined;
    if (!slotId || !activeSlot || activeSlot.isStreaming || activeSlot.stopping || activeSlot.messages.length > 0) {
      slotId = store.createSlot(null);
      store.setActiveSlot(slotId);
    }
    if (agentId) {
      store.updateSlot(slotId, {
        threadAgentId: agentId,
        agentStreamTools:
          store.agentStreamTools === null ? null : [...store.agentStreamTools],
      });
    } else if (projectId) {
      store.updateSlot(slotId, { projectId });
    }

    // 1. Set collection filters so they scope the AI query
    // The handed-over scope replaces whatever an earlier chat left in the
    // filters; with no scope the question searches everything.
    const collections = pending.pageContext.collections ?? [];
    const selectedNodes = pending.pageContext.selectedNodes ?? [];
    if (collections.length === 0 && selectedNodes.length === 0) {
      store.setFilters({ apps: [], kb: [] });
    }
    if (collections.length > 0) {
      store.setFilters({ apps: [...new Set(collections.map((c) => c.id))], kb: [] });

      const cache = { ...store.collectionNamesCache };
      collections.forEach((c) => {
        cache[c.id] = c.name;
      });
      store.setCollectionNamesCache(cache);

      // Store for the streaming UI (pending collection cards on the message)
      store.updateSlot(slotId, {
        pendingCollections: collections.map((c) => ({
          id: c.id,
          name: c.name,
          kind: 'collectionRoot' as const,
        })),
      });
    }

    if (selectedNodes.length > 0) {
      restoreAppliedFilters(appliedFiltersFromNodes(selectedNodes), false);
    }

    // 2. Apply any settings overrides from the widget
    if (pending.settings) {
      if (pending.settings.mode) store.setMode(pending.settings.mode);
      if (pending.settings.queryMode) store.setQueryMode(pending.settings.queryMode);
      if (pending.settings.agentStrategy) store.setAgentStrategy(pending.settings.agentStrategy);
    }

    if (!pending.message.trim() && !pending.attachments?.length) return;

    // 3. Auto-send the message through the runtime. Attachments arrive
    // pre-uploaded (the widget triggered the upload at attach-time), so we
    // forward the refs verbatim — same shape as a regular send from the
    // main composer.
    aui.thread().append({
      role: 'user',
      content: [{ type: 'text', text: pending.message }],
      metadata: {
        custom: {
          collections: collections.length > 0 ? collections : undefined,
          attachments:
            pending.attachments && pending.attachments.length > 0
              ? pending.attachments
              : undefined,
        },
      },
      startRun: true,
    });
  }, [conversationId, aui, activeSlotId, agentId, projectId]);

  const isMobile = useIsMobile();
  const isBelowDesktop = useIsBelowDesktop();
  const agentContextDisplayName = useChatStore((s) => s.agentContextDisplayName);
  const agentContextCreatedBy = useChatStore((s) => s.agentContextCreatedBy);
  const [agentCreatorName, setAgentCreatorName] = useState<string | null>(null);
  const [agentCreatorAvatarUrl, setAgentCreatorAvatarUrl] = useState<string | undefined>(
    undefined
  );

  useEffect(() => {
    const mongoUserId = historyAndShareAgentId ? agentContextCreatedBy : null;
    if (!mongoUserId) {
      setAgentCreatorName(null);
      setAgentCreatorAvatarUrl(undefined);
      return;
    }
    let cancelled = false;
    UsersApi.getUsersByIds([mongoUserId])
      .then((users) => {
        if (cancelled) return;
        const user = users[0];
        setAgentCreatorName(user?.name?.trim() || user?.email?.trim() || null);
        setAgentCreatorAvatarUrl(user?.profilePicture ?? undefined);
      })
      .catch(() => {
        if (!cancelled) {
          setAgentCreatorName(null);
          setAgentCreatorAvatarUrl(undefined);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [historyAndShareAgentId, agentContextCreatedBy]);

  // Render decisions
  /** Profile from GET /api/v1/users/:id — auth-store `user` is often null (not persisted with tokens). */
  const profile = useUserStore((s) => s.profile);
  const isAdmin = useUserStore(selectIsAdmin);
  const demoDataActive = useDemoDataActive();
  // Unknown reads as shown, as before the switch existed.
  const demoHidden = useDemoDataStatus()?.include === false;
  const greetingName = useMemo(() => {
    if (!profile) return '';
    const full = profile.fullName?.trim();
    if (full) return full;
    const first = profile.firstName?.trim();
    if (first) return first;
    const email = profile.email?.trim();
    if (email?.includes('@')) {
      const local = email.split('@')[0];
      if (local) return local;
    }
    return '';
  }, [profile]);

  // Share state
  const [isShareSidebarOpen, setIsShareSidebarOpen] = useState(false);
  const [sharedMembers, setSharedMembers] = useState<SharedAvatarMember[]>([]);

  const chatShareAdapter = useMemo(() => {
    if (!conversationId) return null;
    return createChatShareAdapter(
      conversationId,
      historyAndShareAgentId ? { agentId: historyAndShareAgentId } : undefined
    );
  }, [conversationId, historyAndShareAgentId]);

  // Only the owner sees the Share button, for plain and agent chats alike. Agent chats are shared
  // read-only: the backend refuses `write`, and a recipient gets no composer (see showChatInput).
  const showConversationShare = Boolean(
    conversationId && chatShareAdapter && activeSlotIsOwner === true
  );

  useEffect(() => {
    if (!showConversationShare && isShareSidebarOpen) {
      setIsShareSidebarOpen(false);
    }
  }, [showConversationShare, isShareSidebarOpen]);

  const handleShareClick = useCallback(() => {
    if (!chatShareAdapter) return;
    setIsShareSidebarOpen(true);
  }, [chatShareAdapter]);

  // ── Load shared members for header avatars ───────────────────────
  // Fires whenever the active conversation changes. Uses the same
  // getSharedMembers() path as the share sidebar so IDs stay consistent.
  useEffect(() => {
    if (!conversationId || !chatShareAdapter || !showConversationShare) {
      setSharedMembers([]);
      return;
    }

    let cancelled = false;

    chatShareAdapter.getSharedMembers().then((members) => {
      if (cancelled) return;
      // Exclude the owner from the avatar row (same shape as onShareSuccess)
      setSharedMembers(
        members
          .filter((m) => !m.isOwner)
          .map((m) => ({
            id: m.id,
            name: m.name,
            avatarUrl: m.avatarUrl || undefined,
            type: m.type,
          }))
      );
    }).catch(() => {
      // Non-fatal — header just shows without avatars
    });

    return () => {
      cancelled = true;
    };
  }, [conversationId, chatShareAdapter, showConversationShare]);

  // Show new chat view when no active slot, or slot is new with no messages
  const showNewChatView = !activeSlotId || (
    hasActiveSlot &&
    activeSlotIsTemp &&
    activeSlotMsgCount === 0 &&
    !activeSlotIsStreaming
  );

  /** New-chat landing (main or `?agentId=`): input sits in the centered hero with the greeting;
   * after the first message it renders in the fixed bottom slot (`showNewChatView` false). */
  const isInputCentered = showNewChatView;

  // Show loading state when slot exists but hasn't loaded history yet
  const showLoading = hasActiveSlot && !activeSlotIsInitialized;

  // Initial-load gate: when the URL carries a conversationId on first render,
  // the URL → store sync effect hasn't attached the slot yet, so we'd briefly
  // flash the "new chat" view. Render a full-page loader until the slot
  // attaches AND its history has finished loading.
  const showInitialLoading =
    conversationId != null && (!activeSlotId || !activeSlotIsInitialized);

  // Hide chat input when viewing a shared conversation the user does not own.
  // `null` means "not yet known": an existing chat that is still loading must not show the
  // composer, or a recipient sees it until the chat arrives. Otherwise (a new chat, or a chat
  // whose ownership could not be read) it stays visible.
  const showChatInput =
    activeSlotIsOwner !== false &&
    !(activeSlotIsOwner === null && (showInitialLoading || showLoading));

  // Search mode: show results view when in search mode with results/in-progress search
  const mode = useChatStore((s) => s.settings.mode);
  const hasSearchResults = useChatStore((s) => s.searchResults.length > 0);
  const isSearching = useChatStore((s) => s.isSearching);
  const showSearchView = mode === 'search' && (hasSearchResults || isSearching) && !conversationId;

  // ── Split-pane layout: chat left | drag handle | preview right ────────────
  // Desktop only: on phones and tablets the two columns leave the document too narrow.
  const showSplitPane = !!previewFile && previewMode === 'sidebar' && !isBelowDesktop;

  // ── Auto-close preview when the active conversation changes ──────────────
  // Tracks the previous conversationId so we only clear on actual changes,
  // not on the initial mount.
  const prevConversationIdRef = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (prevConversationIdRef.current === undefined) {
      // First render — just record the current value, no action
      prevConversationIdRef.current = conversationId;
      return;
    }
    if (prevConversationIdRef.current !== conversationId) {
      prevConversationIdRef.current = conversationId;
      clearPreview();
    }
  }, [conversationId, clearPreview]);

  // ── Collapse nav sidebar when split-pane preview opens, restore on close ──
  const didAutoCollapseNavRef = useRef(false);
  useEffect(() => {
    const store = useSidebarWidthStore.getState();
    if (showSplitPane) {
      if (!store.isNavCollapsed) {
        store.setNavCollapsed(true);
        didAutoCollapseNavRef.current = true;
      }
    } else {
      if (didAutoCollapseNavRef.current) {
        store.setNavCollapsed(false);
        didAutoCollapseNavRef.current = false;
      }
    }
  }, [showSplitPane]);

  // If the user manually expands the sidebar while the preview is open, forget
  // that we auto-collapsed it so closing the preview doesn't re-collapse it.
  const prevIsNavCollapsedRef = useRef(isNavCollapsed);
  useEffect(() => {
    const wasCollapsed = prevIsNavCollapsedRef.current;
    prevIsNavCollapsedRef.current = isNavCollapsed;
    if (
      showSplitPane &&
      didAutoCollapseNavRef.current &&
      wasCollapsed &&
      !isNavCollapsed
    ) {
      // User manually expanded → relinquish ownership of the collapsed state
      didAutoCollapseNavRef.current = false;
    }
  }, [isNavCollapsed, showSplitPane]);
  // ─────────────────────────────────────────────────────────────────────────

  // A smaller window (or a width saved on a bigger screen) must still leave the preview room.
  const [chatPanelWidthPx, setChatPanelWidthPx] = useSplitWidth(() => {
    if (typeof window === 'undefined') return CHAT_PANEL_DEFAULT_PX;
    const saved = parseInt(localStorage.getItem(CHAT_PANEL_LS_KEY) ?? '', 10);
    return Number.isFinite(saved) && saved >= CHAT_PANEL_MIN_PX ? saved : CHAT_PANEL_DEFAULT_PX;
  });
  const chatPanelWidthRef = useRef(chatPanelWidthPx);
  useLayoutEffect(() => {
    chatPanelWidthRef.current = chatPanelWidthPx;
  }, [chatPanelWidthPx]);

  // Holds the in-progress drag cleanup so useEffect can cancel it if the
  // component unmounts while the user is dragging the resize handle.
  const splitResizeCleanupRef = useRef<(() => void) | null>(null);

  const beginSplitResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = chatPanelWidthRef.current;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    let finalW = startW;
    const move = (ev: PointerEvent) => {
      finalW = clampSplitWidth(Math.max(CHAT_PANEL_MIN_PX, startW + (ev.clientX - startX)), window.innerWidth);
      setChatPanelWidthPx(finalW);
    };
    const cleanup = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', cleanup);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      splitResizeCleanupRef.current = null;
      try {
        localStorage.setItem(CHAT_PANEL_LS_KEY, String(finalW));
      } catch {
        /* ignore */
      }
    };
    splitResizeCleanupRef.current = cleanup;
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', cleanup);
  }, []);

  // Cancel any in-flight drag if the component unmounts mid-gesture
  useEffect(() => () => { splitResizeCleanupRef.current?.(); }, []);
  // ─────────────────────────────────────────────────────────────────────────

  const showMessageList = !(showInitialLoading || showLoading) && !showSearchView && !showNewChatView;
  const composerColumn = (
    <Box
      style={{
        ...chatContentColumnStyle(),
        paddingTop: !isInputCentered && showChatInput ? 'var(--space-3)' : undefined,
        paddingBottom: 'var(--space-4)',
        // MessageList's composer dock passes clicks through its gutters.
        pointerEvents: 'auto',
      }}
    >
      {!isInputCentered && showChatInput && <ChatInputWrapper />}
      {/* On a phone the links only take room from an open conversation. */}
      {!(isMobile && conversationId) && <ChatFooterLinks />}
    </Box>
  );

  // ── Chat column body (shared between split-pane and full-width modes) ──────
  const chatColumnBody = (
    <>
      {/* Top-left of the chat column (position:relative parent) so it never
          overlaps the agent header or share buttons on the right. */}
      <SidebarExpandButton />

      {historyAndShareAgentId && (
        <AgentChatHeader
          agentId={historyAndShareAgentId}
          displayName={agentContextDisplayName}
          isMobile={isMobile}
          hasExpandButton={!isMobile && isNavCollapsed}
          readOnly={activeSlotIsOwner === false}
          creatorName={agentCreatorName}
          hasShareGroup={!!showConversationShare}
        />
      )}

      {/* Agent creator chip and share group, top-right. */}
      <ConversationHeaderOverlay
        conversationId={conversationId}
        agentChip={
          historyAndShareAgentId && agentCreatorName ? (
            <Tooltip content={`${t('agentBuilder.createdBy')}: ${agentCreatorName}`}>
              <Flex
                align="center"
                gap="2"
                px="2"
                py="1"
                style={{
                  background: 'var(--color-panel)',
                  borderRadius: 'var(--radius-2)',
                  maxWidth: isMobile ? 140 : 220,
                  cursor: 'default',
                }}
              >
                <Avatar
                  size="1"
                  fallback={agentCreatorName.charAt(0).toUpperCase()}
                  src={agentCreatorAvatarUrl}
                  radius="full"
                  style={{ flexShrink: 0 }}
                />
                <Text
                  size="2"
                  style={{
                    color: 'var(--gray-12)',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {agentCreatorName}
                </Text>
              </Flex>
            </Tooltip>
          ) : null
        }
        shareGroup={
          showConversationShare ? (
            <ShareHeaderGroup members={sharedMembers} onShareClick={handleShareClick} />
          ) : null
        }
      />

      {/* Full-width pane: message list scrolls here (scrollbar on the pane edge).
          Message content + composer share chatContentColumnStyle so widths match. */}
      <Flex
        direction="column"
        style={{
          flex: 1,
          minHeight: 0,
          width: '100%',
          position: 'relative',
          zIndex: 10,
          overflow: 'hidden',
          paddingTop: showNewChatView
            ? undefined
            : isMobile
              ? historyAndShareAgentId
                ? '76px'
                : '60px'
              : historyAndShareAgentId
                ? '56px'
                : '40px',
        }}
      >
        {showInitialLoading || showLoading ? (
          <Flex
            direction="column"
            align="center"
            justify="center"
            style={{ flex: 1, width: '100%' }}
          >
            <LottieLoader variant="loader" size={48} showLabel />
          </Flex>
        ) : showSearchView ? (
          <Flex
            direction="column"
            align="center"
            style={{ flex: 1, minHeight: 0, width: '100%', overflow: 'hidden' }}
          >
            <Box style={{ ...chatContentColumnStyle(), flex: 1, minHeight: 0, display: 'flex' }}>
              <SearchResultsView />
            </Box>
          </Flex>
        ) : showNewChatView ? (
          <Flex
            direction="column"
            align="center"
            justify="center"
            style={{
              flex: 1,
              width: '100%',
              marginTop: isMobile ? '0' : '-40px',
            }}
          >
            <Box style={{ ...chatContentColumnStyle() }}>
              <Flex direction="column" align="center" style={{ width: '100%' }}>
                {projectId ? (
                  <>
                    <Flex
                      align="center"
                      gap="2"
                      style={{ marginBottom: 'var(--space-4)', cursor: 'pointer' }}
                      onClick={() => router.push(`/projects/?projectId=${encodeURIComponent(projectId)}`)}
                    >
                      <MaterialIcon
                        name="folder"
                        size={28}
                        color={projectColor || 'var(--accent-9)'}
                      />
                      <Text size="5" weight="bold" style={{ color: 'var(--slate-12)' }}>
                        {projectName ?? '…'}
                      </Text>
                      <MaterialIcon name="settings" size={16} color="var(--slate-9)" />
                    </Flex>
                  </>
                ) : (
                  <>
                    <Box style={{ marginBottom: 'var(--space-4)' }}>
                      <LottieLoader
                        autoplay
                        loop
                        style={{ width: isMobile ? 64 : 80, height: isMobile ? 64 : 80 }}
                      />
                    </Box>
                    <Box
                      style={{
                        textAlign: 'center',
                        marginBottom: isMobile ? 'var(--space-5)' : 'var(--space-6)',
                        fontFamily: 'Manrope, sans-serif',
                      }}
                    >
                      <Text
                        size="4"
                        weight="medium"
                        style={{ color: 'var(--slate-12)', display: 'block', marginBottom: 'var(--space-1)' }}
                      >
                        {t('chat.heyUser', { name: greetingName || t('chat.heyUserDefaultName') })}
                      </Text>
                      <Text size="4" weight="medium" style={{ color: 'var(--slate-12)' }}>
                        {t('chat.greeting')}
                      </Text>
                    </Box>
                  </>
                )}
                {isInputCentered && showChatInput && (
                  <Box style={{ width: '100%' }}>
                    <ChatInputWrapper />
                  </Box>
                )}
                {showChatInput && (
                  // Shows itself only when it applies, including for a disabled demo
                  // whose records are still searchable. Not tied to this admin's own
                  // switch: others may still show it, and its sample accounts can sign in.
                  <DemoDataRemovalNotice isAdmin={isAdmin} style={{ marginTop: 'var(--space-5)' }} />
                )}
                {demoDataActive && showChatInput && !demoHidden && (
                  <DemoSuggestions isAdmin={isAdmin} isMobile={isMobile} onPick={handleSuggestionClick} />
                )}
              </Flex>
            </Box>
          </Flex>
        ) : (
          <Box style={{ flex: 1, minHeight: 0, width: '100%', overflow: 'hidden', display: 'flex' }}>
            <MessageList composer={composerColumn} />
          </Box>
        )}

        {!showMessageList && (
          <Flex
            direction="column"
            align="center"
            className="chat-composer-row"
            style={{
              width: '100%',
              flexShrink: 0,
              paddingBottom: isMobile ? 'max(var(--space-2), env(safe-area-inset-bottom, 0px))' : 0,
            }}
          >
            {composerColumn}
          </Flex>
        )}
      </Flex>
    </>
  );
  // ─────────────────────────────────────────────────────────────────────────

  // Props for the full-screen / phone preview overlay.
  const previewProps = previewFile ? {
    source: 'chat' as const,
    file: {
      id: previewFile.id,
      name: previewFile.name,
      url: previewFile.url,
      blob: previewFile.blob,
      type: previewFile.type,
      size: previewFile.size,
      webUrl: previewFile.webUrl,
      previewRenderable: previewFile.previewRenderable,
      version: previewFile.version,
    },
    isLoading: previewFile.isLoading,
    error: previewFile.error,
    recordDetails: previewFile.recordDetails,
    initialPage: previewFile.initialPage,
    highlightBox: previewFile.highlightBox,
    citations: previewFile.citations,
    initialCitationId: previewFile.initialCitationId,
    hideFileDetails: previewFile.hideFileDetails,
    showDownload: previewFile.showDownload,
    latestVersion: previewFile.latestVersion,
    onVersionChange: previewFile.onVersionChange,
    isSwitchingVersion: previewFile.isSwitchingVersion,
    defaultTab: 'preview' as const,
  } : null;

  return (
    <Flex
      direction="column"
      style={{
        height: '100%',
        width: '100%',
        position: 'relative',
        overflow: 'hidden',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >
      {/*
       * The chat column is ALWAYS the first child of this row so React keeps
       * the same DOM node across split-pane transitions — this preserves the
       * MessageList scroll position when the preview opens or closes.
       * Only the flex sizing and the presence of the right-side panels change.
       */}
      <Flex direction="row" style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        {/* Chat column — stable DOM node */}
        <Flex
          direction="column"
          align="stretch"
          style={{
            flex: showSplitPane ? `0 0 ${chatPanelWidthPx}px` : '1',
            minWidth: showSplitPane ? `${CHAT_PANEL_MIN_PX}px` : undefined,
            height: '100%',
            position: 'relative',
            overflow: 'hidden',
          }}
        >
          {chatColumnBody}
        </Flex>

        {/* Drag handle + preview panel — appear when split-pane is active */}
        {showSplitPane && (
          <>
            {/* Drag handle — 8px visible bar inside a 16px hit area */}
            <Box
              role="separator"
              aria-orientation="vertical"
              aria-label={t('chat.resizePanels')}
              onPointerDown={beginSplitResize}
              style={{
                width: '16px',
                margin: '0 -4px',
                padding: '0 4px',
                boxSizing: 'border-box',
                backgroundClip: 'content-box',
                flexShrink: 0,
                alignSelf: 'stretch',
                cursor: 'col-resize',
                touchAction: 'none',
                zIndex: 10,
                position: 'relative',
                backgroundColor: 'var(--olive-3)',
              }}
              onPointerEnter={(ev) => {
                ev.currentTarget.style.backgroundColor = 'var(--olive-5)';
              }}
              onPointerLeave={(ev) => {
                ev.currentTarget.style.backgroundColor = 'var(--olive-3)';
              }}
            />

            {/* Right panel — slides in from right on mount */}
            <Box
              style={{
                flex: 1,
                minWidth: 0,
                height: '100%',
                overflow: 'hidden',
                position: 'relative',
                animation: 'slideInFromRight 0.28s cubic-bezier(0.4, 0, 0.2, 1)',
              }}
            >
              {/* previewFile is always truthy here because showSplitPane = !!previewFile && ...
                  The extra guard is needed for TypeScript to narrow the type. */}
              {previewFile && (
                <FilePreviewInlinePanel
                  source="chat"
                  file={{
                    id: previewFile.id,
                    name: previewFile.name,
                    url: previewFile.url,
                    blob: previewFile.blob,
                    type: previewFile.type,
                    size: previewFile.size,
                    webUrl: previewFile.webUrl,
                    previewRenderable: previewFile.previewRenderable,
                    version: previewFile.version,
                  }}
                  isLoading={previewFile.isLoading}
                  error={previewFile.error}
                  recordDetails={previewFile.recordDetails}
                  initialPage={previewFile.initialPage}
                  highlightBox={previewFile.highlightBox}
                  citations={previewFile.citations}
                  initialCitationId={previewFile.initialCitationId}
                  hideFileDetails={previewFile.hideFileDetails}
                  showDownload={previewFile.showDownload}
                  latestVersion={previewFile.latestVersion}
                  onVersionChange={previewFile.onVersionChange}
                  isSwitchingVersion={previewFile.isSwitchingVersion}
                  defaultTab="preview"
                  onToggleFullscreen={() => setPreviewMode('fullscreen')}
                  onClose={() => clearPreview()}
                />
              )}
            </Box>
          </>
        )}
      </Flex>

      {/* File Preview - full-screen overlay.
          Shown when the user entered fullscreen, and below desktop in place of
          the split pane. Phones get the mobile layout (citations in a sheet). */}
      {previewProps && (previewMode === 'fullscreen' || (isBelowDesktop && previewMode === 'sidebar')) && (
        isMobile ? (
          <FilePreviewMobile
            {...previewProps}
            open
            onOpenChange={(open) => {
              if (!open) clearPreview();
            }}
          />
        ) : (
          <FilePreviewFullscreen
            {...previewProps}
            onExitFullscreen={isBelowDesktop ? undefined : () => setPreviewMode('sidebar')}
            onClose={() => clearPreview()}
          />
        )
      )}

      {/* Share Sidebar */}
      {showConversationShare && chatShareAdapter && (
        <ShareSidebar
          open={isShareSidebarOpen}
          onOpenChange={setIsShareSidebarOpen}
          adapter={chatShareAdapter}
          onShareSuccess={() => {
            chatShareAdapter.getSharedMembers().then((members) => {
              setSharedMembers(
                members
                  .filter((m) => !m.isOwner)
                  .map((m) => ({
                    id: m.id,
                    name: m.name,
                    avatarUrl: m.avatarUrl || undefined,
                    type: m.type,
                  }))
              );
            });
          }}
        />
      )}

      {/* Command palette overlay (⌘+K) */}
      <ChatSearch
        open={isCommandPaletteOpen}
        onClose={() => setIsCommandPaletteOpen(false)}
      />
    </Flex>
  );
}

/**
 * Main chat page component.
 *
 * Uses a single `useExternalStoreRuntime` that reads from the active
 * slot in Zustand. Thread switching = swap activeSlotId → runtime
 * reactively picks up new slot's messages. One rerender per switch.
 */
export default function ChatPage() {
  debugLog.tick('[chat] [ChatPage]');

  const activeSlotId = useChatStore((s) => s.activeSlotId);

  // Re-build config when active slot's messages or streaming state changes
  const activeMessages = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.messages : undefined
  );
  const activeIsStreaming = useChatStore((s) =>
    s.activeSlotId ? s.slots[s.activeSlotId]?.isStreaming : false
  );

  const liveConfig = useMemo(
    () => buildExternalStoreConfig(activeSlotId),
    [activeSlotId, activeMessages, activeIsStreaming]
  );

  const runtime = useExternalStoreRuntime(liveConfig);

  return (
    <ServiceGate services={['query']}>
      <AssistantRuntimeProvider runtime={runtime}>
        <Suspense>
          <ChatContent />
        </Suspense>
      </AssistantRuntimeProvider>
    </ServiceGate>
  );
}
