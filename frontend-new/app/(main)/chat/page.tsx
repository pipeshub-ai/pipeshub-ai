'use client';

import React, { useEffect, useCallback, useRef, useMemo, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useThreadRuntime } from '@assistant-ui/react';
import { SuggestionChip, MessageList, ChatInputWrapper, SearchResultsView } from './components';
import { AgentChatHeader } from './components/agent-chat-header';
import { useChatStore, ctxKeyFromAgent } from '@/chat/store';
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
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { FilePreviewSidebar, FilePreviewFullscreen } from '@/app/components/file-preview';
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
import { ServiceGate } from '@/app/components/ui/service-gate';
import { SIDEBAR_CONVERSATIONS_PAGE_SIZE } from './constants';

// Space reserved below content views to clear the absolutely-positioned chat input.
const CHAT_INPUT_OFFSET = { mobile: 120, desktop: 128 };
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
function ChatContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
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
      console.error('Failed to fetch conversations:', error);
      setConversationsError(error instanceof Error ? error.message : 'Failed to fetch conversations');
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
          store.hydrateAgentChatResources({
            toolCatalogFullNames: toolFullNames,
            toolGroups,
            connectors,
            kbIds,
            knowledgeCollectionRows: collectionRows,
            knowledgeDefaults: knowledgeDefaultsForStore,
          });
          store.setAgentContextDisplayName(agent?.name?.trim() || null);
        } catch (error) {
          if (!cancelled) {
            console.error('Failed to fetch agent details:', error);
            store.hydrateAgentChatResources(null);
            store.setAgentContextDisplayName(null);
          }
        }
      } else {
        store.hydrateAgentChatResources(null);
        store.setAgentContextDisplayName(null);
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
        if (!cancelled) {
          console.error('Failed to fetch models for context', ctxKey, error);
        }
      }
    };

    load();

    return () => {
      cancelled = true;
    };
  }, [agentId]);

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
        const formattedMessages = loadHistoricalMessages(messages);
        useChatStore.getState().updateSlot(activeSlotId, {
          messages: formattedMessages,
          isInitialized: true,
          hasLoaded: true,
          isOwner,
        });
      } catch (error) {
        console.error('Failed to load conversation history:', error);
        if (!cancelled) {
          // Mark as initialized even on error to avoid infinite retries
          useChatStore.getState().updateSlot(activeSlotId, {
            isInitialized: true,
            isOwner: false,
          });
        }
      }
    };

    loadHistory();

    return () => {
      cancelled = true;
    };
  }, [activeSlotId, hasActiveSlot, activeSlotIsInitialized, activeSlotIsTemp, activeSlotConvId, historyAndShareAgentId]);

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

    // 3. Auto-send the message through the runtime
    threadRuntime.append({
      role: 'user',
      content: [{ type: 'text', text: pending.message }],
      metadata: {
        custom: {
          collections: collections.length > 0 ? collections : undefined,
        },
      },
      startRun: true,
    });
  }, [conversationId, threadRuntime, activeSlotId, agentId]);

  const isMobile = useIsMobile();
  const agentContextDisplayName = useChatStore((s) => s.agentContextDisplayName);

  // Render decisions
  const { t } = useTranslation();
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

  const showConversationShare =
    Boolean(
      conversationId &&
        chatShareAdapter &&
        activeSlotIsOwner === true
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

  // Search mode: show results view when in search mode with results/in-progress search
  const mode = useChatStore((s) => s.settings.mode);
  const hasSearchResults = useChatStore((s) => s.searchResults.length > 0);
  const isSearching = useChatStore((s) => s.isSearching);
  const showSearchView = mode === 'search' && (hasSearchResults || isSearching) && !conversationId;

  return (
    <Flex
      direction="column"
      align="center"
      style={{
        height: '100%',
        width: '100%',
        position: 'relative',
        overflow: 'hidden',
        background: 'linear-gradient(to bottom, var(--olive-2), var(--olive-1))',
      }}
    >

      {historyAndShareAgentId && (
        <AgentChatHeader
          agentId={historyAndShareAgentId}
          displayName={agentContextDisplayName}
          isMobile={isMobile}
        />
      )}

      {/* Share header group — owners only (hidden for Shared Chats / shared-with-me). */}
      {showConversationShare && (
        <Box
          style={{
            position: 'absolute',
            top: 12,
            right: 16,
            zIndex: 20,
          }}
        >
          <ShareHeaderGroup
            members={sharedMembers}
            onShareClick={handleShareClick}
          />
        </Box>
      )}
      {showInitialLoading ? (
        /* Initial page load — conversationId in URL but slot/history not ready yet */
        <Flex
          direction="column"
          align="center"
          justify="center"
          style={{
            flex: 1,
            position: 'relative',
            zIndex: 10,
            width: '100%',
          }}
        >
          <LottieLoader variant="loader" size={48} showLabel />
        </Flex>
      ) : showSearchView ? (
        /* Search Results View */
        <Flex direction="column" style={{
          flex: 1,
          width: '100%',
          overflow: 'hidden',
          marginBottom: `${(isMobile ? CHAT_INPUT_OFFSET.mobile : CHAT_INPUT_OFFSET.desktop) + (isMobile ? SEARCH_RESULTS_EXTRA_OFFSET.mobile : SEARCH_RESULTS_EXTRA_OFFSET.desktop)}px`,
        }}>
          <SearchResultsView />
        </Flex>
      ) : showNewChatView ? (
        /* New Chat View */
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
              : isMobile ? (historyAndShareAgentId ? '36px' : '0') : historyAndShareAgentId ? '-44px' : '-80px',
            paddingBottom: isInputCentered ? '0' : isMobile ? '140px' : '0',
            width: '100%',
          }}
        >
          {/* Logo */}
          <Box style={{ marginBottom: 'var(--space-4)' }}>
            <LottieLoader autoplay loop style={{ width: isMobile ? 64 : 80, height: isMobile ? 64 : 80 }} />
          </Box>

          {/* Greeting */}
          <Box
            style={{
              textAlign: 'center',
              marginBottom: isInputCentered ? (isMobile ? 'var(--space-5)' : 'var(--space-6)') : isMobile ? 'var(--space-8)' : '48px',
              fontFamily: 'Manrope, sans-serif',
              padding: isMobile ? '0 var(--space-4)' : undefined,
            }}
          >
            <Text
              size="4"
              weight="medium"
              style={{
                color: 'var(--slate-12)',
                display: 'block',
                marginBottom: 'var(--space-1)',
              }}
            >
              {t('chat.heyUser', { name: greetingName || t('chat.heyUserDefaultName') })}
            </Text>
            <Text size="4" weight="medium" style={{ color: 'var(--slate-12)' }}>
              {t('chat.greeting')}
            </Text>
          </Box>

          {/* Centered chat input — new-chat landing (assistant or agent); moves to bottom after first send. */}
          {isInputCentered && (
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

          {/* Suggestion chips are intentionally hidden: the defaults are hardcoded
              placeholders and not tied to the user's actual data yet. Re-enable
              once suggestions are dynamically generated. */}
        </Flex>
      ) : showLoading ? (
        /* Loading View */
        <Flex
          direction="column"
          align="center"
          justify="center"
          style={{
            flex: 1,
            position: 'relative',
            zIndex: 10,
          }}
        >
          <LottieLoader variant="loader" size={48} showLabel />
        </Flex>
      ) : (
        /* Conversation View */
        <Flex
          direction="column"
          style={{
            flex: 1,
            position: 'relative',
            zIndex: 10,
            width: '100%',
            overflow: 'hidden',
            marginBottom: `${isMobile ? CHAT_INPUT_OFFSET.mobile : CHAT_INPUT_OFFSET.desktop}px`,
            paddingTop: isMobile ? (historyAndShareAgentId ? '76px' : '60px') : historyAndShareAgentId ? '56px' : '40px',
          }}
        >
          <MessageList />
        </Flex>
      )}

      {/* Chat input: fixed bottom when a thread has started; on new-chat landing it lives in the hero (`isInputCentered`). */}
      <Box
        style={{
          position: 'absolute',
          bottom: isMobile ? 0 : 'var(--space-4)',
          left: isMobile ? 0 : '50%',
          right: isMobile ? 0 : undefined,
          transform: isMobile ? undefined : 'translateX(-50%)',
          padding: isMobile ? '0 var(--space-4) var(--space-4)' : undefined,
          zIndex: 20,
        }}
      >
        {!isInputCentered && <ChatInputWrapper />}
        <ChatFooterLinks />
      </Box>

      {/* File Preview - Sidebar Mode */}
      {previewFile && previewMode === 'sidebar' && (
        <FilePreviewSidebar
          open={true}
          source="chat"
          file={{
            id: previewFile.id,
            name: previewFile.name,
            url: previewFile.url,
            blob: previewFile.blob,
            type: previewFile.type,
            size: previewFile.size,
            webUrl: previewFile.webUrl,
          }}
          isLoading={previewFile.isLoading}
          error={previewFile.error}
          recordDetails={previewFile.recordDetails}
          initialPage={previewFile.initialPage}
          highlightBox={previewFile.highlightBox}
          citations={previewFile.citations}
          initialCitationId={previewFile.initialCitationId}
          hideFileDetails={previewFile.hideFileDetails}
          defaultTab="preview"
          onToggleFullscreen={() => setPreviewMode('fullscreen')}
          onOpenChange={(open) => {
            if (!open) clearPreview();
          }}
        />
      )}

      {/* File Preview - Fullscreen Mode */}
      {previewFile && previewMode === 'fullscreen' && (
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
          }}
          isLoading={previewFile.isLoading}
          error={previewFile.error}
          recordDetails={previewFile.recordDetails}
          initialPage={previewFile.initialPage}
          highlightBox={previewFile.highlightBox}
          citations={previewFile.citations}
          initialCitationId={previewFile.initialCitationId}
          hideFileDetails={previewFile.hideFileDetails}
          defaultTab="preview"
          onExitFullscreen={() => setPreviewMode('sidebar')}
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
