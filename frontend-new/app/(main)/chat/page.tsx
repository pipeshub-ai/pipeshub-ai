'use client';

import React, { useEffect, useCallback, useRef, useMemo, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { AssistantRuntimeProvider, useExternalStoreRuntime, useThreadRuntime } from '@assistant-ui/react';
import { SuggestionChip, MessageList, ChatInputWrapper, SearchResultsView } from './components';
import { AgentChatHeader } from './components/agent-chat-header';
import { useChatStore } from '@/chat/store';
import { ChatSuggestion } from '@/chat/types';
import { ChatApi } from '@/chat/api';
import { buildChatHref } from '@/chat/build-chat-url';
import { AgentsApi } from '@/app/(main)/agents/api';
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

// Space reserved below content views to clear the absolutely-positioned chat input.
const CHAT_INPUT_OFFSET = { mobile: 120, desktop: 160 };
// Extra breathing room above the chat input for the search results list.
const SEARCH_RESULTS_EXTRA_OFFSET = { mobile: 0, desktop: 70 };

// Background decorative pattern
const BackgroundPattern = ({ showNewChatView }: { showNewChatView: boolean }) => (
  <Box
    style={{
      position: 'absolute',
      inset: 0,
      overflow: 'hidden',
      pointerEvents: 'none',
    }}
  >
    {showNewChatView ? (
      <img
        src="/background/chat-bg.svg"
        alt=""
        style={{
          position: 'absolute',
          bottom: 0,
          left: '50%',
          transform: 'translateX(-50%)',
          width: '100%',
          height: 'auto',
          opacity: 1,
        }}
      />
    ) : (
      <Box
        style={{
          position: 'absolute',
          bottom: 0,
          left: '50%',
          transform: 'translateX(-50%)',
          width: '200%',
          height: '300px',
          opacity: 0.3,
          background: 'radial-gradient(ellipse at center bottom, rgba(11, 122, 89, 0.15) 0%, transparent 70%)',
        }}
      />
    )}
  </Box>
);

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
      style={{ marginTop: 'var(--space-2)', paddingBottom: 'var(--space-1)' }}
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

  // Agent tools + display name for header (sidebar also loads tools when listing convs).
  useEffect(() => {
    if (!agentId) {
      useChatStore.getState().setAgentStreamTools([]);
      useChatStore.getState().setAgentContextDisplayName(null);
      return;
    }
    let cancelled = false;
    AgentsApi.getAgent(agentId).then((res) => {
      if (!cancelled) {
        useChatStore.getState().setAgentStreamTools(res.toolFullNames);
        useChatStore.getState().setAgentContextDisplayName(res.agent?.name?.trim() || null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [agentId]);
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
      const result = await ChatApi.fetchConversations();
      setConversations(result.conversations);
      setSharedConversations(result.sharedConversations);
      setPagination(result.pagination);
    } catch (error) {
      console.error('Failed to fetch conversations:', error);
      setConversationsError(error instanceof Error ? error.message : 'Failed to fetch conversations');
    } finally {
      setIsConversationsLoading(false);
    }
  }, [setConversations, setSharedConversations, setIsConversationsLoading, setConversationsError, setPagination]);

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

  // Fetch available LLMs and store the default model (agent-aware)
  useEffect(() => {
    let cancelled = false;

    const fetchDefaultModel = async () => {
      try {
        let models;
        
        if (agentId?.trim()) {
          // Agent context: fetch agent details and use configured models
          const { agent } = await AgentsApi.getAgent(agentId);
          if (cancelled) return;
          
          if (!agent?.models || agent.models.length === 0) {
            console.error('Agent has no configured models:', agentId);
            return;
          }
          
          models = agent.models;
        } else {
          // Regular chat: use all org models
          models = await ChatApi.fetchAvailableLlms();
          if (cancelled) return;
          
          if (models.length === 0) {
            console.error('No org models available');
            return;
          }
        }

        // Find and set default model
        const defaultModel = models.find((m) => m.isDefault);
        if (defaultModel && !cancelled) {
          useChatStore.getState().setDefaultModel({
            modelKey: defaultModel.modelKey,
            modelName: defaultModel.modelName,
            modelFriendlyName: defaultModel.modelFriendlyName || defaultModel.modelName,
            modelProvider: defaultModel.provider,
          });
        }
      } catch (error) {
        if (!cancelled) {
          console.error('Failed to fetch default model:', error);
        }
      }
    };

    fetchDefaultModel();

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
        const toolsPatch =
          urlAgentId && store.agentStreamTools.length > 0
            ? { agentStreamTools: [...store.agentStreamTools] }
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
              store.agentStreamTools.length > 0 ? [...store.agentStreamTools] : null,
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
        const messages = historyAndShareAgentId
          ? (await AgentsApi.fetchAgentConversation(historyAndShareAgentId, convId)).messages
          : (await ChatApi.fetchConversation(convId)).messages;
        if (cancelled) return;

        const formattedMessages = loadHistoricalMessages(messages);
        useChatStore.getState().updateSlot(activeSlotId, {
          messages: formattedMessages,
          isInitialized: true,
          hasLoaded: true,
        });
      } catch (error) {
        console.error('Failed to load conversation history:', error);
        if (!cancelled) {
          // Mark as initialized even on error to avoid infinite retries
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
          agentStreamTools: [...store.agentStreamTools],
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
        agentStreamTools: [...store.agentStreamTools],
      });
    }

    // 1. Set collection filters so they scope the AI query
    const collections = pending.pageContext.collections ?? [];
    if (collections.length > 0) {
      const kbIds = collections.map((c) => c.id);
      store.setFilters({ ...store.settings.filters, kb: kbIds });

      const cache = { ...store.collectionNamesCache };
      collections.forEach((c) => {
        cache[c.id] = c.name;
      });
      store.setCollectionNamesCache(cache);

      // Store for the streaming UI (pending collection cards on the message)
      store.updateSlot(slotId, {
        pendingCollections: collections,
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

    // 4. Clear KB filters after send
    if (collections.length > 0) {
      store.setFilters({ ...store.settings.filters, kb: [] });
    }
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

  const handleShareClick = useCallback(() => {
    if (!chatShareAdapter) return;
    setIsShareSidebarOpen(true);
  }, [chatShareAdapter]);

  // ── Load shared members for header avatars ───────────────────────
  // Fires whenever the active conversation changes. Uses the same
  // getSharedMembers() path as the share sidebar so IDs stay consistent.
  useEffect(() => {
    if (!conversationId || !chatShareAdapter) {
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
  }, [conversationId, chatShareAdapter]);

  // Show new chat view when no active slot, or slot is new with no messages
  const showNewChatView = !activeSlotId || (
    hasActiveSlot &&
    activeSlotIsTemp &&
    activeSlotMsgCount === 0 &&
    !activeSlotIsStreaming
  );

  // Show loading state when slot exists but hasn't loaded history yet
  const showLoading = hasActiveSlot && !activeSlotIsInitialized;

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
      <BackgroundPattern showNewChatView={showNewChatView} />

      {agentId && (
        <AgentChatHeader
          agentId={agentId}
          displayName={agentContextDisplayName}
          isMobile={isMobile}
        />
      )}

      {/* Share header group — shown when there's an active conversation */}
      {conversationId && chatShareAdapter && (
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
      {showSearchView ? (
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
            marginTop: isMobile ? (agentId ? '36px' : '0') : agentId ? '-44px' : '-80px',
            paddingBottom: isMobile ? '140px' : '0',
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
              marginBottom: isMobile ? '32px' : '48px',
              fontFamily: 'Manrope, sans-serif',
              padding: isMobile ? '0 var(--space-4)' : undefined,
            }}
          >
            <Text
              size="4"
              weight="medium"
              style={{
                color: 'var(--slate-11)',
                display: 'block',
                marginBottom: 'var(--space-1)',
              }}
            >
              {t('chat.heyUser', { name: greetingName || t('chat.heyUserDefaultName') })}
            </Text>
            <Text size="4" weight="medium" style={{ color: 'var(--slate-11)' }}>
              {t('chat.greeting')}
            </Text>
          </Box>

          {/* Suggestion Chips */}
          {isMobile ? (
            /* Mobile: vertical single-column list, full-width */
            <Flex
              direction="column"
              gap="2"
              style={{
                width: '100%',
                padding: '0 var(--space-4)',
              }}
            >
              {defaultSuggestions.slice(0, 5).map((suggestion) => (
                <SuggestionChip
                  key={suggestion.id}
                  text={suggestion.text}
                  icons={[...suggestion.icons]}
                  onClick={() => handleSuggestionClick(suggestion)}
                  fullWidth
                />
              ))}
            </Flex>
          ) : (
            /* Desktop: 2-column paired rows */
            <Flex direction="column" align="center" gap="1" style={{ marginBottom: 'var(--space-6)' }}>
              {/* Row 1 */}
              <Flex align="center" gap="1">
                <SuggestionChip
                  text={defaultSuggestions[0].text}
                  icons={[...defaultSuggestions[0].icons]}
                  onClick={() => handleSuggestionClick(defaultSuggestions[0])}
                />
                <SuggestionChip
                  text={defaultSuggestions[1].text}
                  icons={[...defaultSuggestions[1].icons]}
                  onClick={() => handleSuggestionClick(defaultSuggestions[1])}
                />
              </Flex>

              {/* Row 2 */}
              <Flex align="center" gap="1">
                <SuggestionChip
                  text={defaultSuggestions[2].text}
                  icons={[...defaultSuggestions[2].icons]}
                  onClick={() => handleSuggestionClick(defaultSuggestions[2])}
                />
                <SuggestionChip
                  text={defaultSuggestions[3].text}
                  icons={[...defaultSuggestions[3].icons]}
                  onClick={() => handleSuggestionClick(defaultSuggestions[3])}
                />
              </Flex>

              {/* Row 3 */}
              <Flex align="center" gap="1">
                <SuggestionChip
                  text={defaultSuggestions[4].text}
                  icons={[...defaultSuggestions[4].icons]}
                  onClick={() => handleSuggestionClick(defaultSuggestions[4])}
                />
                <SuggestionChip
                  text={defaultSuggestions[5].text}
                  icons={[...defaultSuggestions[5].icons]}
                  onClick={() => handleSuggestionClick(defaultSuggestions[5])}
                />
              </Flex>
            </Flex>
          )}
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
            paddingTop: isMobile ? (agentId ? '76px' : '60px') : agentId ? '72px' : '56px',
          }}
        >
          <MessageList />
        </Flex>
      )}

      {/* Chat Input - Fixed at bottom, uses ChatInputWrapper to access runtime */}
      <Box
        style={{
          position: 'absolute',
          bottom: isMobile ? 0 : '48px',
          left: isMobile ? 0 : '50%',
          right: isMobile ? 0 : undefined,
          transform: isMobile ? undefined : 'translateX(-50%)',
          padding: isMobile ? '0 var(--space-4) var(--space-4)' : undefined,
          zIndex: 20,
        }}
      >
        <ChatInputWrapper />
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
            type: previewFile.type,
            size: previewFile.size,
          }}
          isLoading={previewFile.isLoading}
          recordDetails={previewFile.recordDetails}
          initialPage={previewFile.initialPage}
          highlightBox={previewFile.highlightBox}
          citations={previewFile.citations}
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
            type: previewFile.type,
            size: previewFile.size,
          }}
          isLoading={previewFile.isLoading}
          recordDetails={previewFile.recordDetails}
          initialPage={previewFile.initialPage}
          highlightBox={previewFile.highlightBox}
          citations={previewFile.citations}
          defaultTab="preview"
          onClose={() => clearPreview()}
        />
      )}

      {/* Share Sidebar */}
      {chatShareAdapter && (
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
    <AssistantRuntimeProvider runtime={runtime}>
      <Suspense>
        <ChatContent />
      </Suspense>
    </AssistantRuntimeProvider>
  );
}
