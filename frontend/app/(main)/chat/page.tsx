'use client';

import React, { useEffect, useCallback, useLayoutEffect, useRef, useMemo, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useThreadRuntime } from '@assistant-ui/react';
import { SuggestionChip, MessageList, ChatInputWrapper, SearchResultsView } from './components';
import { AgentChatHeader } from './components/agent-chat-header';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
import {
  applyConversationModelInfoToStore,
  findModelInfoInConversationLists,
  pickModelInfoFromConversationBundle,
} from '@/chat/utils/apply-conversation-model-info';
import { ChatSuggestion } from '@/chat/types';
import { ChatApi } from '@/chat/api';
import { buildChatHref } from '@/chat/build-chat-url';
import {
  AgentsApi,
  buildAgentChatToolGroups,
  extractAgentKnowledgeDefaults,
  extractAgentKnowledgeConnectors,
  extractAgentKnowledgeCollectionRows,
} from '@/app/(main)/agents/api';
import { fetchModelsForContext } from '@/chat/utils/fetch-models-for-context';
import { buildExternalStoreConfig, loadHistoricalMessages } from '@/chat/runtime';
import { debugLog } from '@/chat/debug-logger';
import { useCommandStore } from '@/lib/store/command-store';
import { usePendingChatStore } from '@/lib/store/pending-chat-store';
import { useSidebarWidthStore } from '@/lib/store/sidebar-width-store';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { Flex, Box, Text, Avatar, Tooltip, IconButton } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { FilePreviewInlinePanel, FilePreviewFullscreen } from '@/app/components/file-preview';
import { ShareSidebar, ShareHeaderGroup } from '@/app/components/share';
import type { SharedAvatarMember } from '@/app/components/share';
import { createChatShareAdapter } from './share-adapter';
import { ChatSearch } from './components/search';
import { isCommandKey } from '@/lib/utils/platform';
import { LottieLoader } from '@/app/components/ui/lottie-loader';
import { useGitHubStars } from '@/app/components/workspace-menu/hooks/use-github-stars';
import { EXTERNAL_LINKS } from '@/lib/constants/external-links';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useUserStore } from '@/lib/store/user-store';
import { toast } from '@/lib/store/toast-store';
import { ServiceGate } from '@/app/components/ui/service-gate';
import { useServicesHealthStore } from '@/lib/store/services-health-store';
import { SIDEBAR_CONVERSATIONS_PAGE_SIZE } from './constants';
import { useGraphUserEntry } from '@/lib/hooks/use-graph-user-entry';

// Space reserved below content views to clear the absolutely-positioned chat input.
const CHAT_INPUT_OFFSET = { mobile: 120, desktop: 128 };
// Space reserved when input is hidden — just enough to clear the footer links.
const FOOTER_ONLY_OFFSET = { mobile: 48, desktop: 48 };
// Extra breathing room above the chat input for the search results list.
const SEARCH_RESULTS_EXTRA_OFFSET = { mobile: 0, desktop: 70 };

const footerLinkStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 'var(--space-1)',
  opacity: 0.7,
  textDecoration: 'none',
  color: 'inherit',
};

function ChatFooterLinks() {
  const stars = useGitHubStars();

  return (
    <Flex
      align="center"
      justify="center"
      gap="3"
      style={{ marginTop: 'var(--space-1)', paddingBottom: 0 }}
    >
      <a
        href={EXTERNAL_LINKS.github}
        target="_blank"
        rel="noopener noreferrer"
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

      <Text style={{ color: 'var(--olive-6)', fontSize: 12 }}>·</Text>

      <a
        href="https://docs.pipeshub.com/introduction"
        target="_blank"
        rel="noopener noreferrer"
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
          Docs
        </span>
      </a>
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

/** Clamp n between min and max (inclusive). */
function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n));
}

function ChatContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const { t } = useTranslation();
  const conversationId = searchParams.get('conversationId');
  const rawAgentParam = searchParams.get('agentId');
  const agentId = rawAgentParam?.trim() ? rawAgentParam : null;

  const threadRuntime = useThreadRuntime();

  // ── Narrow selectors: only re-render when the selected value changes ──
  // Actions are stable refs in Zustand — selecting them individually
  // prevents this component from re-rendering on background slot updates.
  const previewFile = useChatStore((s) => s.previewFile);
  const previewMode = useChatStore((s) => s.previewMode);
  const setConversations = useChatStore((s) => s.setConversations);
  const setSharedConversations = useChatStore((s) => s.setSharedConversations);
  const setIsConversationsLoading = useChatStore((s) => s.setIsConversationsLoading);
  const setConversationsError = useChatStore((s) => s.setConversationsError);
  const setPagination = useChatStore((s) => s.setPagination);
  const setSharedPagination = useChatStore((s) => s.setSharedPagination);
  const setPreviewMode = useChatStore((s) => s.setPreviewMode);
  const clearPreview = useChatStore((s) => s.clearPreview);

  // Nav sidebar collapse state — used to show the expand button when collapsed
  const isNavCollapsed = useSidebarWidthStore((s) => s.isNavCollapsed);
  const setNavCollapsed = useSidebarWidthStore((s) => s.setNavCollapsed);

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
      const store = useChatStore.getState();

      // 0. Reset search mode if active (URL won't change since both are /chat)
      if (store.settings.mode === 'search') {
        store.setMode('chat');
        store.clearSearchResults();
      }

      const rawAgentInUrl =
        typeof window !== 'undefined'
          ? new URLSearchParams(window.location.search).get('agentId')
          : null;
      const agentIdInUrl = rawAgentInUrl?.trim() ? rawAgentInUrl : null;

      // 1. Detach visible thread only — background streams keep running (parallel chats)
      store.clearActiveSlot();

      // 2–3. Sync URL: stay on agent new-chat when agentId present, else main home
      if (agentIdInUrl) {
        const href = buildChatHref({ agentId: agentIdInUrl });
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

  // Fetch conversations from API
  const loadConversations = useCallback(async () => {
    setIsConversationsLoading(true);
    setConversationsError(null);

    try {
      const [owned, shared] = await Promise.all([
        ChatApi.fetchConversations(1, SIDEBAR_CONVERSATIONS_PAGE_SIZE, { source: 'owned' }),
        ChatApi.fetchConversations(1, SIDEBAR_CONVERSATIONS_PAGE_SIZE, { source: 'shared' }),
      ]);
      setConversations(owned.conversations);
      setSharedConversations(shared.conversations);
      setPagination(owned.pagination);
      setSharedPagination(shared.pagination);
    } catch (error) {
      if (useServicesHealthStore.getState().apiServerReachable) {
        console.error('Failed to fetch conversations:', error);
        setConversationsError(error instanceof Error ? error.message : 'Failed to fetch conversations');
      }
    } finally {
      setIsConversationsLoading(false);
    }
  }, [setConversations, setSharedConversations, setIsConversationsLoading, setConversationsError, setPagination, setSharedPagination]);

  // Fetch conversations on mount
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Re-fetch conversations when a mutation bumps the version counter
  const conversationsVersion = useChatStore((s) => s.conversationsVersion);
  useEffect(() => {
    if (conversationsVersion > 0) {
      loadConversations();
    }
  }, [conversationsVersion, loadConversations]);

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
          const { agent, toolFullNames } = await AgentsApi.getAgent(agentId);
          if (cancelled) return;
          const knowledgeDefaults = extractAgentKnowledgeDefaults(agent);
          const collectionRows = extractAgentKnowledgeCollectionRows(agent);
          const kbIds =
            collectionRows.length > 0
              ? collectionRows.map((r) => r.id)
              : knowledgeDefaults.kb;
          const knowledgeDefaultsForStore = {
            apps: knowledgeDefaults.apps,
            kb: kbIds,
          };
          const connectors = extractAgentKnowledgeConnectors(agent);
          const toolGroups = buildAgentChatToolGroups(agent);
          const deprecatedToolNames = (agent?.toolsets ?? [])
            .flatMap((ts) => ts.tools ?? [])
            .filter((tool) => tool.deprecated === true)
            .map((tool) => tool.name);
          store.hydrateAgentChatResources({
            toolCatalogFullNames: toolFullNames,
            toolGroups,
            connectors,
            kbIds,
            knowledgeCollectionRows: collectionRows,
            knowledgeDefaults: knowledgeDefaultsForStore,
            deprecatedToolNames,
          });
          store.setAgentContextDisplayName(agent?.name?.trim() || null);
          store.setAgentContextCreatedBy(agent?.createdBy ?? null);

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
              if (af) {
                freshStore.setAgentKnowledgeScope({
                  apps: af.apps.map((n) => n.id),
                  kb: af.kb.map((n) => n.id),
                });
                const namesCache: Record<string, string> = {};
                const metaCache: Record<string, { name: string; nodeType: string; connector: string }> = {};
                for (const node of [...af.apps, ...af.kb]) {
                  namesCache[node.id] = node.name;
                  metaCache[node.id] = { name: node.name, nodeType: node.nodeType, connector: node.connector };
                }
                freshStore.setCollectionNamesCache(namesCache);
                freshStore.setCollectionMetaCache(metaCache);
              }
            }
          }
        } catch (error) {
          if (!cancelled) {
            console.error('Failed to fetch agent details:', error);
            store.hydrateAgentChatResources(null);
            store.setAgentContextDisplayName(null);
            store.setAgentContextCreatedBy(null);
          }
        }
      } else {
        store.hydrateAgentChatResources(null);
        store.setAgentContextDisplayName(null);
        store.setAgentContextCreatedBy(null);
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
  }, [agentId, router, t]);

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
      // Clear any filters left over from the previous conversation so that
      // the SelectedCollections pills don't bleed into the new-chat landing.
      store.setFilters({ apps: [], kb: [] });

      const activeSlot = store.activeSlotId ? store.slots[store.activeSlotId] : null;
      if (agentId) {
        store.setAgentKnowledgeScope(null);
        if (store.activeSlotId) {
          debugLog.flush('chat-switch', { from: store.activeSlotId, to: null, reason: 'agent-new-chat-url' });
          store.clearActiveSlot();
        }
      } else if (store.activeSlotId && activeSlot?.threadAgentId) {
        debugLog.flush('chat-switch', { from: store.activeSlotId, to: null, reason: 'leave-agent-for-main-home' });
        useChatStore.setState({ activeSlotId: null });
        store.bumpConversationsVersion();
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
                (msg.metadata as { custom?: { appliedFilters?: { apps: { id: string; name: string; nodeType: string; connector: string }[]; kb: { id: string; name: string; nodeType: string; connector: string }[] } } } | undefined)
                  ?.custom?.appliedFilters != null
            );
          const af = (lastWithFilters?.metadata as { custom?: { appliedFilters?: { apps: { id: string; name: string; nodeType: string; connector: string }[]; kb: { id: string; name: string; nodeType: string; connector: string }[] } } } | undefined)
            ?.custom?.appliedFilters;
          if (af) {
            // Legacy chats stored the KB/Collections root ID in apps. With the new
            // behavior, KB roots are never added to apps — drop them for compat.
            const legacyFilteredApps = af.apps.filter(
              (n) => (n.connector ?? '').trim().toUpperCase() !== 'KB'
            );
            if (urlAgentId) {
              store.setAgentKnowledgeScope({
                apps: legacyFilteredApps.map((n) => n.id),
                kb: af.kb.map((n) => n.id),
              });
            } else {
              store.setFilters({
                apps: legacyFilteredApps.map((n) => n.id),
                kb: af.kb.map((n) => n.id),
              });
            }
            const namesCache: Record<string, string> = {};
            const metaCache: Record<string, { name: string; nodeType: string; connector: string }> = {};
            for (const node of [...legacyFilteredApps, ...af.kb]) {
              namesCache[node.id] = node.name;
              metaCache[node.id] = { name: node.name, nodeType: node.nodeType, connector: node.connector };
            }
            store.setCollectionNamesCache(namesCache);
            store.setCollectionMetaCache(metaCache);
          } else {
            if (urlAgentId) {
              store.setAgentKnowledgeScope(null);
            } else {
              store.setFilters({ apps: [], kb: [] });
            }
          }
        }
      } else {
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
  }, [conversationId, agentId]);

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
        const q = new URLSearchParams();
        if (aid) q.set('agentId', aid);
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

    const loadHistory = async () => {
      try {
        const detail = historyAndShareAgentId
          ? await AgentsApi.fetchAgentConversation(historyAndShareAgentId, convId)
          : await ChatApi.fetchConversation(convId);
        if (cancelled) return;

        const messages = detail.messages;
        const isOwner = detail.conversation.access?.isOwner ?? false;
        const modelInfo = pickModelInfoFromConversationBundle({
          modelInfo: detail.conversation.modelInfo,
          messages: detail.messages,
        });

        // Always reset filters so pills from a previously viewed conversation
        // don't persist if this conversation carries no filter history.
        useChatStore.getState().setFilters({ apps: [], kb: [] });

        // Restore filter state from the most recent user message that carried filters.
        // This brings back the selected connectors/collections after a hard refresh.
        const lastFiltered = [...messages]
          .reverse()
          .find((msg) => msg.messageType === 'user_query' && msg.appliedFilters);

        if (lastFiltered?.appliedFilters) {
          const af = lastFiltered.appliedFilters;
          const store = useChatStore.getState();

          // Legacy chats stored the KB/Collections root ID in apps. With the new
          // behavior, KB roots are never added to apps — drop them for compat.
          const legacyFilteredApps = af.apps.filter(
            (n) => (n.connector ?? '').trim().toUpperCase() !== 'KB'
          );

          if (historyAndShareAgentId) {
            store.setAgentKnowledgeScope({
              apps: legacyFilteredApps.map((n) => n.id),
              kb: af.kb.map((n) => n.id),
            });
          } else {
            store.setFilters({
              apps: legacyFilteredApps.map((n) => n.id),
              kb: af.kb.map((n) => n.id),
            });
          }

          const namesCache: Record<string, string> = {};
          const metaCache: Record<string, { name: string; nodeType: string; connector: string }> = {};
          for (const node of [...legacyFilteredApps, ...af.kb]) {
            namesCache[node.id] = node.name;
            metaCache[node.id] = {
              name: node.name,
              nodeType: node.nodeType,
              connector: node.connector,
            };
          }
          store.setCollectionNamesCache(namesCache);
          store.setCollectionMetaCache(metaCache);
        }

        const formattedMessages = loadHistoricalMessages(messages);
        useChatStore.getState().updateSlot(activeSlotId, {
          messages: formattedMessages,
          isInitialized: true,
          hasLoaded: true,
          isOwner,
          ...(modelInfo ? { conversationModelInfo: modelInfo } : {}),
        });
      } catch (error) {
        console.error('Failed to load conversation history:', error);
        if (!cancelled) {
          // Mark as initialized to avoid infinite retries, but leave isOwner
          // untouched: a transient fetch failure shouldn't flip the share
          // button off for an actual owner.
          useChatStore.getState().updateSlot(activeSlotId, {
            isInitialized: true,
          });
        }
      }
    };

    loadHistory();

    return () => {
      cancelled = true;
    };
  }, [activeSlotId, hasActiveSlot, activeSlotIsInitialized, activeSlotIsTemp, activeSlotConvId, historyAndShareAgentId]);

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
  useEffect(() => {
    if (!activeSlotId || !activeSlotConversationModelInfo) return;
    applyConversationModelInfoToStore(activeSlotConversationModelInfo, modelCtxKey);
  }, [activeSlotId, activeSlotConversationModelInfo, modelCtxKey]);

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
      }
    }

    threadRuntime.append({
      role: 'user',
      content: [{ type: 'text', text: suggestion.text }],
      startRun: true,
    });
  };

  // ── Consume pending chat context from widget ──────────────────────
  const pendingConsumedRef = useRef(false);
  useEffect(() => {
    if (conversationId || pendingConsumedRef.current) return;

    const pending = usePendingChatStore.getState().consumePending();
    if (!pending) return;
    pendingConsumedRef.current = true;

    const store = useChatStore.getState();

    // Ensure we have a slot for the new chat
    let slotId = store.activeSlotId;
    if (!slotId) {
      slotId = store.createSlot(null);
      store.setActiveSlot(slotId);
    }
    if (agentId) {
      store.updateSlot(slotId, {
        threadAgentId: agentId,
        agentStreamTools:
          store.agentStreamTools === null ? null : [...store.agentStreamTools],
      });
    }

    // 1. Set collection filters so they scope the AI query
    const collections = pending.pageContext.collections ?? [];
    if (collections.length > 0) {
      const rootIds = collections.map((c) => c.id);
      store.setFilters({
        ...store.settings.filters,
        apps: [...new Set([...(store.settings.filters.apps ?? []), ...rootIds])],
        kb: [],
      });

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

    // 2. Apply any settings overrides from the widget
    if (pending.settings) {
      if (pending.settings.mode) store.setMode(pending.settings.mode);
      if (pending.settings.queryMode) store.setQueryMode(pending.settings.queryMode);
      if (pending.settings.agentStrategy) store.setAgentStrategy(pending.settings.agentStrategy);
    }

    // 3. Auto-send the message through the runtime. Attachments arrive
    // pre-uploaded (the widget triggered the upload at attach-time), so we
    // forward the refs verbatim — same shape as a regular send from the
    // main composer.
    threadRuntime.append({
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
  }, [conversationId, threadRuntime, activeSlotId, agentId]);

  const isMobile = useIsMobile();
  const agentContextDisplayName = useChatStore((s) => s.agentContextDisplayName);
  const agentContextCreatedBy = useChatStore((s) => s.agentContextCreatedBy);
  const agentCreatorEntry = useGraphUserEntry(historyAndShareAgentId ? agentContextCreatedBy : null);
  const agentCreatorAvatarUrl =
    agentCreatorEntry?.profilePicture ??
    (agentCreatorEntry?.mongoId ? `/api/v1/users/${agentCreatorEntry.mongoId}/dp` : undefined);

  // Render decisions
  /** Profile from GET /api/v1/users/:id — auth-store `user` is often null (not persisted with tokens). */
  const profile = useUserStore((s) => s.profile);
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

  const defaultSuggestionsMap = t('chat.defaultSuggestions', { returnObjects: true }) as Record<string, { text: string; icons: ChatSuggestion['icons'] }>;
  const defaultSuggestions: ChatSuggestion[] = Object.entries(defaultSuggestionsMap).map(([id, item]) => ({
    id,
    text: item.text,
    icons: item.icons,
  }));

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

  // Agent threads are not shareable by anyone (including the owner), so gate on
  // historyAndShareAgentId (slot-scoped, set for both URL and restored agent threads).
  const showConversationShare =
    Boolean(
      conversationId &&
        chatShareAdapter &&
        activeSlotIsOwner === true &&
        !historyAndShareAgentId
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

  // Hide chat input when viewing a shared conversation the user does not own.
  // `null` means "not yet known" (loading) — keep input visible to avoid flash.
  const showChatInput = activeSlotIsOwner !== false;

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

  // Search mode: show results view when in search mode with results/in-progress search
  const mode = useChatStore((s) => s.settings.mode);
  const hasSearchResults = useChatStore((s) => s.searchResults.length > 0);
  const isSearching = useChatStore((s) => s.isSearching);
  const showSearchView = mode === 'search' && (hasSearchResults || isSearching) && !conversationId;

  // ── Split-pane layout: chat left | drag handle | preview right ────────────
  // Active on desktop when a file preview is open in 'sidebar' mode.
  const showSplitPane = !!previewFile && previewMode === 'sidebar' && !isMobile;

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

  const [chatPanelWidthPx, setChatPanelWidthPx] = useState<number>(() => {
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
      finalW = clamp(startW + (ev.clientX - startX), CHAT_PANEL_MIN_PX, window.innerWidth * 0.7);
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

  // ── Chat column body (shared between split-pane and full-width modes) ──────
  const chatColumnBody = (
    <>
      {/* Sidebar expand button — desktop only, shown when nav is collapsed.
          Positioned at top-left of the chat column (position:relative parent)
          so it never overlaps the agent header or share buttons on the right. */}
      {!isMobile && isNavCollapsed && (
        <Box
          style={{
            position: 'absolute',
            top: 10,
            left: 12,
            zIndex: 25,
          }}
        >
          <Tooltip content="Expand sidebar" side="right">
            <IconButton
              variant="ghost"
              color="gray"
              size="2"
              aria-label="Expand sidebar"
              onClick={() => setNavCollapsed(false)}
              style={{ margin: 0 }}
            >
              <MaterialIcon name="menu" size={20} color="var(--gray-11)" />
            </IconButton>
          </Tooltip>
        </Box>
      )}

      {historyAndShareAgentId && (
        <AgentChatHeader
          agentId={historyAndShareAgentId}
          displayName={agentContextDisplayName}
          isMobile={isMobile}
          hasExpandButton={!isMobile && isNavCollapsed}
        />
      )}

      {/* Agent creator chip */}
      {historyAndShareAgentId && agentCreatorEntry?.fullName && (
        <Box
          style={{
            position: 'absolute',
            top: 10,
            right: showConversationShare ? 200 : 16,
            zIndex: 19,
          }}
        >
          <Tooltip content={`${t('agentBuilder.createdBy')}: ${agentCreatorEntry.fullName}`}>
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
                fallback={agentCreatorEntry.fullName.charAt(0).toUpperCase()}
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
                {agentCreatorEntry.fullName}
              </Text>
            </Flex>
          </Tooltip>
        </Box>
      )}

      {/* Share header group — owners only */}
      {showConversationShare && (
        <Box style={{ position: 'absolute', top: 12, right: 16, zIndex: 20 }}>
          <ShareHeaderGroup members={sharedMembers} onShareClick={handleShareClick} />
        </Box>
      )}

      {showInitialLoading ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          style={{ flex: 1, position: 'relative', zIndex: 10, width: '100%' }}
        >
          <LottieLoader variant="loader" size={48} showLabel />
        </Flex>
      ) : showSearchView ? (
        <Flex
          direction="column"
          style={{
            flex: 1,
            width: '100%',
            overflow: 'hidden',
            marginBottom: showChatInput
              ? `${(isMobile ? CHAT_INPUT_OFFSET.mobile : CHAT_INPUT_OFFSET.desktop) + (isMobile ? SEARCH_RESULTS_EXTRA_OFFSET.mobile : SEARCH_RESULTS_EXTRA_OFFSET.desktop)}px`
              : `${isMobile ? FOOTER_ONLY_OFFSET.mobile : FOOTER_ONLY_OFFSET.desktop}px`,
          }}
        >
          <SearchResultsView />
        </Flex>
      ) : showNewChatView ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          style={{
            flex: 1,
            position: 'relative',
            zIndex: 10,
            marginTop: isInputCentered
              ? (isMobile ? '0' : '-40px')
              : isMobile
              ? historyAndShareAgentId
                ? '36px'
                : '0'
              : historyAndShareAgentId
              ? '-44px'
              : '-80px',
            paddingBottom: isInputCentered ? '0' : isMobile ? '140px' : '0',
            width: '100%',
          }}
        >
          <Box style={{ marginBottom: 'var(--space-4)' }}>
            <LottieLoader autoplay loop style={{ width: isMobile ? 64 : 80, height: isMobile ? 64 : 80 }} />
          </Box>

          <Box
            style={{
              textAlign: 'center',
              marginBottom: isInputCentered
                ? isMobile
                  ? 'var(--space-5)'
                  : 'var(--space-6)'
                : isMobile
                ? 'var(--space-8)'
                : '48px',
              fontFamily: 'Manrope, sans-serif',
              padding: isMobile ? '0 var(--space-4)' : undefined,
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

          {isInputCentered && showChatInput && (
            <Box
              style={{
                width: '100%',
                display: 'flex',
                justifyContent: 'center',
                padding: isMobile ? '0 var(--space-4)' : undefined,
              }}
            >
              <ChatInputWrapper />
            </Box>
          )}
        </Flex>
      ) : showLoading ? (
        <Flex
          direction="column"
          align="center"
          justify="center"
          style={{ flex: 1, position: 'relative', zIndex: 10 }}
        >
          <LottieLoader variant="loader" size={48} showLabel />
        </Flex>
      ) : (
        <Flex
          direction="column"
          style={{
            flex: 1,
            position: 'relative',
            zIndex: 10,
            width: '100%',
            overflow: 'hidden',
            minHeight: '300px',
            marginBottom: showChatInput
              ? `${isMobile ? CHAT_INPUT_OFFSET.mobile : CHAT_INPUT_OFFSET.desktop}px`
              : `${isMobile ? FOOTER_ONLY_OFFSET.mobile : FOOTER_ONLY_OFFSET.desktop}px`,
            paddingTop: isMobile
              ? historyAndShareAgentId
                ? '76px'
                : '60px'
              : historyAndShareAgentId
              ? '56px'
              : '40px',
          }}
        >
          <MessageList />
        </Flex>
      )}

      {/* Chat input: spans the full chat column width, content centered within.
          Using left:0/right:0 ensures correct sizing in narrow split-pane mode
          (avoids the 50rem ChatInput overflowing a narrow panel).
          pointerEvents:'none' on the positioning shell makes it transparent to
          clicks in areas where no child element sits (e.g. the scrollbar at the
          right edge), while children restore interactivity with their own default
          pointer-events:auto. */}
      <Box
        style={{
          position: 'absolute',
          bottom: isMobile ? 0 : 'var(--space-4)',
          left: 0,
          right: 0,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          padding: isMobile ? '0 var(--space-4) var(--space-4)' : '0 var(--space-3)',
          zIndex: 20,
          pointerEvents: 'none',
        }}
      >
        <Box style={{ width: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', pointerEvents: 'auto' }}>
          {!isInputCentered && showChatInput && <ChatInputWrapper />}
          <ChatFooterLinks />
        </Box>
      </Box>
    </>
  );
  // ─────────────────────────────────────────────────────────────────────────

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
          align="center"
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
            {/* Drag handle — 8 px hit-area */}
            <Box
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize chat and preview panels"
              onPointerDown={beginSplitResize}
              style={{
                width: '8px',
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
                  defaultTab="preview"
                  onToggleFullscreen={() => setPreviewMode('fullscreen')}
                  onClose={() => clearPreview()}
                />
              )}
            </Box>
          </>
        )}
      </Flex>

      {/* File Preview - Fullscreen overlay.
          Shown in two cases:
          1. Desktop: user explicitly entered fullscreen mode.
          2. Mobile: the split-pane is unavailable, so the sidebar-mode preview
             falls back to this full-screen overlay automatically. */}
      {previewFile && (previewMode === 'fullscreen' || (isMobile && previewMode === 'sidebar')) && (
        <FilePreviewFullscreen
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
          defaultTab="preview"
          onExitFullscreen={isMobile ? undefined : () => setPreviewMode('sidebar')}
          onClose={() => clearPreview()}
        />
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
