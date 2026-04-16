'use client';

import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { useRouter, useSearchParams } from 'next/navigation';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatApi } from '@/chat/api';
import type { Conversation } from '@/chat/types';
import { Box, Flex, Text, TextField, Theme } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useCommandStore } from '@/lib/store/command-store';
import { useToastStore } from '@/lib/store/toast-store';
import { groupConversationsByTime, getNonEmptyGroups } from '@/chat/sidebar/time-group';
import type { TimeGroupKey } from '@/lib/utils/group-by-time';
import { TimeGroupedSkeleton, SearchResultsSkeleton } from './skeleton';
import { ChatRow } from './chat-row';
import { SearchResultRow } from './search-result-row';
import { CommandPalette } from './command-palette';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';

// ── Constants ──

/** Page size for browse + search inside the ⌘+K overlay */
const OVERLAY_CONVERSATIONS_LIMIT = 50;

/** Translations for time-group labels */
const TIME_GROUP_I18N: Record<TimeGroupKey, string> = {
  'Today': 'timeGroup.today',
  'Previous 7 Days': 'timeGroup.previous7Days',
  'Older': 'timeGroup.older',
};

function isAbortOrCancelError(err: unknown): boolean {
  const e = err as { name?: string; code?: string };
  return (
    e.name === 'AbortError' ||
    e.name === 'CanceledError' ||
    e.code === 'ERR_CANCELED'
  );
}

// ── Main component ──

interface ChatSearchProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Chat search overlay — triggered by ⌘+K.
 *
 * Lists come from `ChatApi.fetchConversations` (browse: no search; query: `search` param).
 * Rendered via React Portal to avoid z-index issues.
 */
export function ChatSearch({ open, onClose }: ChatSearchProps) {
  const { appearance } = useThemeAppearance();
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const agentId = searchParams.get('agentId');
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const addToast = useToastStore((s) => s.addToast);

  const [searchQuery, setSearchQuery] = useState('');
  const [contentLeft, setContentLeft] = useState(0);

  const [browseConversations, setBrowseConversations] = useState<Conversation[]>([]);
  const [searchResults, setSearchResults] = useState<Conversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);

  const dispatch = useCommandStore((s) => s.dispatch);

  const debouncedQuery = useDebouncedSearch(searchQuery.trim(), 350);

  // ── Measure main content area offset ──
  useEffect(() => {
    if (!open) return;

    function measure() {
      const contentArea = document.querySelector('[data-main-content]');
      if (contentArea) {
        setContentLeft(contentArea.getBoundingClientRect().left);
      }
    }

    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  }, [open]);

  // Reset input when overlay opens (debounced value follows via useDebouncedSearch; empty clears immediately)
  useEffect(() => {
    if (!open) return;
    setSearchQuery('');
  }, [open]);

  // Browse (no search) vs search: same API; param omitted when debounced query is empty
  useEffect(() => {
    if (!open) return;

    const q = debouncedQuery;
    const isSearch = q.length > 0;

    const ac = new AbortController();
    setConversationsLoading(true);

    (async () => {
      try {
        const result = await ChatApi.fetchConversations(1, OVERLAY_CONVERSATIONS_LIMIT, {
          ...(isSearch ? { search: q } : {}),
          signal: ac.signal,
        });
        const merged = [
          ...result.sharedConversations,
          ...result.conversations,
        ];
        if (isSearch) {
          setSearchResults(merged);
        } else {
          setBrowseConversations(merged);
          setSearchResults([]);
        }
      } catch (err: unknown) {
        if (isAbortOrCancelError(err)) return;
        addToast({
          variant: 'error',
          title: t('message.error'),
          description:
            err instanceof Error
              ? err.message
              : isSearch
                ? 'Search failed'
                : 'Could not load conversations',
        });
        if (isSearch) setSearchResults([]);
      } finally {
        setConversationsLoading(false);
      }
    })();

    return () => ac.abort();
  }, [open, debouncedQuery, addToast, t]);

  // ── Focus input on open ──
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
    }
  }, [open]);

  // ── Escape to close ──
  useEffect(() => {
    if (!open) return;

    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    }

    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, onClose]);

  // ── Body scroll lock ──
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  const timeGroups = useMemo(() => {
    const grouped = groupConversationsByTime(browseConversations);
    return getNonEmptyGroups(grouped);
  }, [browseConversations]);

  const trimmedInput = searchQuery.trim();
  const inSearchMode = trimmedInput.length > 0;
  /** True while debounce hasn’t caught up to the input, or a search request is in flight */
  const searchPending =
    inSearchMode &&
    (debouncedQuery !== trimmedInput || conversationsLoading);

  // ── Handlers ──
  const handleNewChat = useCallback(() => {
    onClose();
    dispatch('newChat');
  }, [onClose, dispatch]);

  const handleSelectConversation = useCallback(
    (id: string) => {
      onClose();
      router.push(
        buildChatHref({
          agentId: agentId || undefined,
          conversationId: id,
        })
      );
    },
    [onClose, router, agentId]
  );

  const handleSearchSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
  }, []);

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
  }, []);

  if (!open) return null;

  const overlay = (
    <Theme
      accentColor="jade"
      grayColor="olive"
      appearance={appearance}
      radius="medium"
    >
      <div
        onClick={(e: React.MouseEvent) => {
          if (e.target === e.currentTarget) onClose();
        }}
        style={{
          position: 'fixed',
          top: 0,
          left: contentLeft,
          right: 0,
          bottom: 0,
          zIndex: 50,
          display: 'flex',
          alignItems: 'flex-start',
          justifyContent: 'center',
          paddingTop: '15vh',
        }}
      >
        <Flex
          ref={panelRef}
          direction="column"
          onClick={(e: React.MouseEvent) => e.stopPropagation()}
          style={{
            width: '37.5rem',
            maxHeight: '512px',
            backdropFilter: 'blur(25px)',
            WebkitBackdropFilter: 'blur(25px)',
            backgroundColor: 'var(--effects-translucent)',
            border: '1px solid var(--olive-3)',
            borderRadius: 'var(--radius-2)',
            boxShadow: '0px 20px 48px 0px var(--black-a6)',
            overflow: 'hidden',
          }}
        >
          {/* Search input */}
          <form onSubmit={handleSearchSubmit}>
            <Box style={{ padding: 'var(--space-3) var(--space-3) 0' }}>
              <TextField.Root
                ref={inputRef}
                size="3"
                placeholder={t('nav.searchChats') + '...'}
                value={searchQuery}
                onChange={handleSearchChange}
                style={{ width: '100%' }}
              >
                <TextField.Slot>
                  <MaterialIcon name="search" size={18} color="var(--slate-a11)" />
                </TextField.Slot>
              </TextField.Root>
            </Box>
          </form>

          {/* Command Palette row (New Chat action) */}
          <CommandPalette onClick={handleNewChat} />

          {/* Scrollable content area */}
          <Box
            className="no-scrollbar"
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '0 var(--space-3) var(--space-3)',
            }}
          >
            {inSearchMode ? (
              searchPending ? (
                <SearchResultsSkeleton />
              ) : searchResults.length > 0 ? (
                <Flex direction="column" gap="1">
                  {searchResults.map((conv) => (
                    <SearchResultRow
                      key={conv.id}
                      conversation={conv}
                      onClick={() => handleSelectConversation(conv.id)}
                    />
                  ))}
                </Flex>
              ) : (
                <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
                  <Text size="2" style={{ color: 'var(--slate-a9)' }}>
                    {t('message.noResults')}
                  </Text>
                </Flex>
              )
            ) : conversationsLoading ? (
              <TimeGroupedSkeleton />
            ) : (
              <Flex direction="column" gap="2">
                {timeGroups.map(([groupKey, groupConversations]) => (
                  <Flex key={groupKey} direction="column">
                    <Flex
                      align="center"
                      style={{
                        height: 32,
                        padding: '0 var(--space-2)',
                      }}
                    >
                      <Text
                        size="1"
                        style={{
                          color: 'var(--slate-a9)',
                          fontWeight: 400,
                        }}
                      >
                        {t(TIME_GROUP_I18N[groupKey])}
                      </Text>
                    </Flex>

                    <Flex direction="column" gap="1">
                      {groupConversations.map((conv) => (
                        <ChatRow
                          key={conv.id}
                          conversation={conv}
                          onClick={() => handleSelectConversation(conv.id)}
                          showDate={false}
                        />
                      ))}
                    </Flex>
                  </Flex>
                ))}

                {timeGroups.length === 0 && !conversationsLoading && (
                  <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
                    <Text size="2" style={{ color: 'var(--slate-a9)' }}>
                      {t('chat.noChatsYet')}
                    </Text>
                  </Flex>
                )}
              </Flex>
            )}
          </Box>
        </Flex>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.4; }
          50% { opacity: 0.8; }
        }
      `}</style>
    </Theme>
  );

  return createPortal(overlay, document.body);
}
