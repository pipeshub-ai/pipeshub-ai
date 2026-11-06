'use client';

import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import React, { useEffect, useLayoutEffect, useRef, useState, useMemo, useCallback } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { buildChatHref } from '@/chat/build-chat-url';
import { ChatApi } from '@/chat/api';
import type { Conversation } from '@/chat/types';
import { Box, Dialog, Flex, IconButton, Text, TextField, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useCommandStore } from '@/lib/store/command-store';
import { useToastStore } from '@/lib/store/toast-store';
import { isRequestCancelledError } from '@/lib/api/api-error';
import { groupConversationsByTime, getNonEmptyGroups } from '@/chat/sidebar/time-group';
import { useLocalDay } from '@/lib/hooks/use-local-day';
import type { TimeGroupKey } from '@/lib/utils/group-by-time';
import { ListSkeleton } from '@/app/components/ui/list-skeleton';
import { useDeferredLoading } from '@/app/components/ui/use-deferred-loading';
import { ChatRow } from './chat-row';
import { SearchResultRow } from './search-result-row';
import { CommandPalette } from './command-palette';
import { CMDK_ROW_CLASS, CMDK_ROW_CSS } from './row-styles';
import { useDebouncedSearch } from '@/knowledge-base/hooks/use-debounced-search';

// ── Constants ──

/** Page size for browse + search inside the ⌘+K overlay */
const OVERLAY_CONVERSATIONS_LIMIT = 50;

/** Translations for time-group labels */
const TIME_GROUP_I18N: Record<TimeGroupKey, string> = {
  'Today': 'timeGroup.today',
  'Yesterday': 'timeGroup.yesterday',
  'Previous 7 Days': 'timeGroup.previous7Days',
  'Older': 'timeGroup.older',
};

/** Marks the search dialog, so its scrim can take a lighter wash than other dialogs. */
const CMDK_DIALOG_CLASS = 'cmdk-dialog';

/**
 * Scoped to this dialog. The scrim is a soft veil (the panel itself is
 * translucent, so a heavy wash would muddy it); Radix draws the overlay with a
 * pseudo-element that inline styles cannot reach.
 */
const CMDK_DIALOG_CSS = `
.rt-DialogOverlay:has(.${CMDK_DIALOG_CLASS})::before {
  background-color: var(--black-a4);
}
${CMDK_ROW_CSS}`;

/** On screen and reachable (not inside an inert or hidden subtree). */
function isFocusable(el: HTMLElement): boolean {
  return el.isConnected && el.getClientRects().length > 0 && !el.closest('[inert], [aria-hidden="true"]');
}

/** A control's visible words, without the icon-font ligatures ("search", "chevron_right"). */
function labelText(el: HTMLElement): string {
  let text = el.textContent ?? '';
  el.querySelectorAll('.material-icons-outlined').forEach((icon) => {
    text = text.replace(icon.textContent ?? '', '');
  });
  return text.trim();
}

/**
 * Where focus goes when the dialog was opened from nowhere (the shortcut pressed
 * with focus on the page body): the sidebar's Search chats row, or, in the drawer
 * layout where that row is hidden, the Open sidebar button. Never the body.
 */
function findFallbackFocus(searchLabel: string, openSidebarLabel: string): HTMLElement | null {
  const candidates = Array.from(document.querySelectorAll<HTMLElement>('button, a[href]')).filter(
    (el) => !el.closest(`.${CMDK_DIALOG_CLASS}`) && isFocusable(el),
  );
  return (
    candidates.find((el) => labelText(el).startsWith(searchLabel)) ??
    candidates.find((el) => el.getAttribute('aria-label') === openSidebarLabel) ??
    null
  );
}

function isAbortOrCancelError(err: unknown): boolean {
  const e = (err ?? {}) as { name?: string; code?: string };
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
 * Chat search dialog, opened by ⌘+K or the sidebar's Search chats row.
 *
 * Lists come from `ChatApi.fetchConversations` (browse: no search; query: `search` param).
 * A Radix dialog: it traps focus, closes on Escape and on the scrim, and hands
 * focus back to whatever opened it.
 */
export function ChatSearch({ open, onClose }: ChatSearchProps) {
  const isMobile = useIsMobile();
  const { t } = useTranslation();
  const router = useRouter();
  const searchParams = useSearchParams();
  const agentId = searchParams.get('agentId');
  const inputRef = useRef<HTMLInputElement>(null);
  /** What had focus before the dialog opened, so closing it can return focus there. */
  const openerRef = useRef<HTMLElement | null>(null);
  const addToast = useToastStore((s) => s.addToast);

  const [searchQuery, setSearchQuery] = useState('');

  const [browseConversations, setBrowseConversations] = useState<Conversation[]>([]);
  const [searchResults, setSearchResults] = useState<Conversation[]>([]);
  const [conversationsLoading, setConversationsLoading] = useState(false);

  const dispatch = useCommandStore((s) => s.dispatch);

  const debouncedQuery = useDebouncedSearch(searchQuery.trim(), 350);

  // Clear the input when the dialog closes, so the next open starts from the plain lists. Clearing on
  // open instead would first run (and then cancel) a search for the last query on every reopen.
  // The debounced value follows at once, since useDebouncedSearch clears empty values immediately.
  useEffect(() => {
    if (open) return;
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
        const [shared, owned] = await Promise.all([
          ChatApi.fetchConversations(1, OVERLAY_CONVERSATIONS_LIMIT, {
            source: 'shared',
            ...(isSearch ? { search: q } : {}),
            signal: ac.signal,
            // This dialog shows its own error toast with the approved copy.
            suppressErrorToast: true,
          }),
          ChatApi.fetchConversations(1, OVERLAY_CONVERSATIONS_LIMIT, {
            source: 'owned',
            ...(isSearch ? { search: q } : {}),
            signal: ac.signal,
            // This dialog shows its own error toast with the approved copy.
            suppressErrorToast: true,
          }),
        ]);
        const merged = [...shared.conversations, ...owned.conversations];
        if (isSearch) {
          setSearchResults(merged);
        } else {
          setBrowseConversations(merged);
          setSearchResults([]);
        }
      } catch (err: unknown) {
        // The API client rejects an aborted request with its own error, not an AbortError.
        if (ac.signal.aborted || isAbortOrCancelError(err) || isRequestCancelledError(err)) return;
        addToast({
          variant: 'error',
          title: t('message.error'),
          // The approved sentence, not the transport's message (which is technical).
          description: isSearch ? t('nav.searchChatsFailed') : t('nav.loadChatsFailed'),
        });
        if (isSearch) setSearchResults([]);
      } finally {
        // An aborted run was replaced by a newer one that is still loading: it owns the flag now.
        if (!ac.signal.aborted) setConversationsLoading(false);
      }
    })();

    return () => ac.abort();
  }, [open, debouncedQuery, addToast, t]);


  // Remember the opener before Radix moves focus into the dialog (layout effects run
  // before the dialog's own focus effect).
  useLayoutEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    openerRef.current = active instanceof HTMLElement && active !== document.body ? active : null;
  }, [open]);

  const today = useLocalDay();
  const timeGroups = useMemo(() => {
    const grouped = groupConversationsByTime(browseConversations, today);
    return getNonEmptyGroups(grouped);
  }, [browseConversations, today]);

  const trimmedInput = searchQuery.trim();
  const inSearchMode = trimmedInput.length > 0;
  /** True while debounce hasn’t caught up to the input, or a search request is in flight */
  const searchPending =
    inSearchMode &&
    (debouncedQuery !== trimmedInput || conversationsLoading);

  const browseLoading = !inSearchMode && conversationsLoading;
  const browseSkeleton = useDeferredLoading(browseLoading);
  const searchSkeleton = useDeferredLoading(searchPending);

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

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) onClose();
    },
    [onClose],
  );

  /** Up and down step through the field, New chat and the chats, in the order Tab uses. */
  const handleArrowKeys = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === 'ArrowDown' ? 1 : e.key === 'ArrowUp' ? -1 : 0;
    if (!step || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || e.nativeEvent.isComposing) return;
    const items = [inputRef.current, ...e.currentTarget.querySelectorAll<HTMLElement>(`.${CMDK_ROW_CLASS}`)];
    const next = items[items.indexOf(document.activeElement as HTMLElement) + step];
    if (!next) return;
    e.preventDefault();
    // The list scrolls it into view by the least amount (its onFocus), not to the centre.
    next.focus({ preventScroll: true });
  }, []);

  const showBrowseSkeleton = browseLoading || browseSkeleton.visible;
  const showSearchSkeleton = searchPending || searchSkeleton.visible;

  return (
    <Dialog.Root open={open} onOpenChange={handleOpenChange}>
      <Dialog.Content
        className={CMDK_DIALOG_CLASS}
        aria-modal="true"
        aria-describedby={undefined}
        onKeyDown={handleArrowKeys}
        onOpenAutoFocus={(e) => {
          e.preventDefault();
          inputRef.current?.focus();
        }}
        onCloseAutoFocus={(e) => {
          // No Dialog.Trigger here (the sidebar row, the ⌘K shortcut and the command
          // store all open it), so hand focus back to whatever had it before.
          e.preventDefault();
          const opener = openerRef.current;
          openerRef.current = null;
          const target =
            opener && isFocusable(opener) ? opener : findFallbackFocus(t('nav.searchChats'), t('sidebar.open'));
          target?.focus();
        }}
        style={{
          // Fixed at 28% of the viewport, centred, so the panel does not jump as the
          // list grows. Centring by margin, not transform: Radix's entrance animation
          // owns the transform.
          position: 'fixed',
          top: '28%',
          left: 0,
          right: 0,
          margin: '0 auto',
          width: 'calc(var(--space-9) * 9 + var(--space-5))',
          maxWidth: 'calc(100vw - 2 * var(--space-4))',
          // Sized to its content, up to 444px: the field, New chat and seven and a half chats (five and a
          // third search hits), so the list ends in a cut row that says it scrolls. On a short window it
          // stops 64px above the bottom, never at it.
          maxHeight: 'min(calc(var(--space-9) * 7 - var(--space-1)), calc(72dvh - var(--space-9)))',
          padding: 'var(--space-4) 0',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          backdropFilter: 'var(--frost-filter)',
          WebkitBackdropFilter: 'var(--frost-filter)',
          backgroundColor: 'var(--frost-bg-strong)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-2)',
        }}
      >
        <style>{CMDK_DIALOG_CSS}</style>
        <VisuallyHidden>
          <Dialog.Title>{t('nav.searchChats')}</Dialog.Title>
        </VisuallyHidden>

        <Flex
          direction="column"
          gap="5"
          style={{ flex: 1, minHeight: 0, padding: '0 var(--space-4)' }}
        >
          {/* Search field and the New chat action */}
          <Flex direction="column" gap="3" style={{ flexShrink: 0 }}>
            <form onSubmit={handleSearchSubmit}>
              <Flex align="center" gap="3">
                <TextField.Root
                  ref={inputRef}
                  size="3"
                  aria-label={t('nav.searchChats')}
                  placeholder={t('nav.searchChatsPlaceholder')}
                  value={searchQuery}
                  onChange={handleSearchChange}
                  style={{ flex: 1, minWidth: 0 }}
                >
                  <TextField.Slot>
                    <MaterialIcon name="search" size={16} color="var(--text-placeholder)" />
                  </TextField.Slot>
                </TextField.Root>
                {/* Phones have no Escape key, and the scrim is a small target. */}
                {isMobile && (
                  <IconButton
                    type="button"
                    variant="ghost"
                    color="gray"
                    className="touch-hit"
                    aria-label={t('common.close')}
                    onClick={onClose}
                    style={{ margin: 0 }}
                  >
                    <MaterialIcon name="close" size={20} color="var(--text-secondary)" />
                  </IconButton>
                )}
              </Flex>
            </form>

            <CommandPalette onClick={handleNewChat} />
          </Flex>

          {/* The list scrolls inside the panel. A row that takes keyboard focus is brought into view,
              also when Shift+Tab wraps from the field to the last row (Radix focuses it without
              scrolling). Not on a click: the cut row must not move under the pointer. */}
          <Box
            style={{ flex: 1, minHeight: 0, overflowY: 'auto' }}
            onFocus={(e) => {
              if (e.target.matches(':focus-visible')) e.target.scrollIntoView({ block: 'nearest' });
            }}
          >
            {inSearchMode ? (
              showSearchSkeleton ? (
                <ListSkeleton
                  variant="two-line"
                  statusLabel={t('nav.loadingChats')}
                  exiting={searchSkeleton.exiting}
                />
              ) : searchResults.length > 0 ? (
                <Flex direction="column">
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
                  <Text size="2" style={{ color: 'var(--text-secondary)' }}>
                    {t('message.noResults')}
                  </Text>
                </Flex>
              )
            ) : showBrowseSkeleton ? (
              <ListSkeleton
                variant="two-line"
                statusLabel={t('nav.loadingChats')}
                exiting={browseSkeleton.exiting}
              />
            ) : (
              <Flex direction="column" gap="5">
                {timeGroups.map(([groupKey, groupConversations]) => (
                  <Flex key={groupKey} direction="column" gap="3">
                    <Text
                      as="div"
                      size="1"
                      weight="medium"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {t(TIME_GROUP_I18N[groupKey])}
                    </Text>
                    {/* Rows sit flush, as in the sidebar: a 12px gap under every row made the list a third taller. */}
                    <Flex direction="column">
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

                {timeGroups.length === 0 && (
                  <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
                    <Text size="2" style={{ color: 'var(--text-secondary)' }}>
                      {t('chat.noChatsYet')}
                    </Text>
                  </Flex>
                )}
              </Flex>
            )}
          </Box>
        </Flex>
      </Dialog.Content>
    </Dialog.Root>
  );
}
