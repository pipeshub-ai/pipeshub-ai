'use client';

import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Box, Flex, Text, TextField, Theme } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useThemeAppearance } from '@/app/components/theme-provider';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { groupConversationsByTime, getNonEmptyGroups } from '@/chat/sidebar/time-group';
import type { TimeGroupKey } from '@/lib/utils/group-by-time';
import { TimeGroupedSkeleton } from '@/chat/components/search/skeleton';
import { ChatRow } from '@/chat/components/search/chat-row';
import { SearchResultRow } from '@/chat/components/search/search-result-row';
import type { Conversation } from '../types';

const TIME_GROUP_I18N: Record<TimeGroupKey, string> = {
  'Today': 'timeGroup.today',
  'Previous 7 Days': 'timeGroup.previous7Days',
  'Older': 'timeGroup.older',
};

interface ArchivedChatSearchProps {
  open: boolean;
  onClose: () => void;
  conversations: Conversation[];
  isLoading: boolean;
  onSelect: (conversationId: string) => void;
}

export function ArchivedChatSearch({
  open,
  onClose,
  conversations,
  isLoading,
  onSelect,
}: ArchivedChatSearchProps) {
  const { appearance } = useThemeAppearance();
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  const [searchQuery, setSearchQuery] = useState('');
  const [contentLeft, setContentLeft] = useState(0);

  const timeGroups = useMemo(() => {
    const grouped = groupConversationsByTime(conversations);
    return getNonEmptyGroups(grouped);
  }, [conversations]);

  // Measure main content area offset
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

  // Focus input on open
  useEffect(() => {
    if (open) {
      setSearchQuery('');
      requestAnimationFrame(() => {
        inputRef.current?.focus();
      });
    }
  }, [open]);

  // Escape to close
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

  // Body scroll lock
  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = '';
    };
  }, [open]);

  const handleSelectConversation = useCallback(
    (id: string) => {
      onClose();
      onSelect(id);
    },
    [onClose, onSelect],
  );

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
  }, []);

  const filteredConversations = useMemo(() => {
    if (!searchQuery.trim()) return null;
    const q = searchQuery.toLowerCase();
    return conversations.filter((c) => c.title.toLowerCase().includes(q));
  }, [searchQuery, conversations]);

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
          <form onSubmit={(e) => e.preventDefault()}>
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

          {/* Scrollable content area */}
          <Box
            className="no-scrollbar"
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: 'var(--space-3)',
            }}
          >
            {isLoading && !filteredConversations ? (
              <TimeGroupedSkeleton />
            ) : filteredConversations ? (
              filteredConversations.length > 0 ? (
                <Flex direction="column" gap="1">
                  {filteredConversations.map((conv) => (
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
            ) : (
              <Flex direction="column" gap="2">
                {timeGroups.map(([groupKey, groupConversations]) => (
                  <Flex key={groupKey} direction="column">
                    <Flex
                      align="center"
                      style={{
                        height: 'var(--space-8)',
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

                {timeGroups.length === 0 && !isLoading && (
                  <Flex align="center" justify="center" style={{ padding: 'var(--space-6)' }}>
                    <Text size="2" style={{ color: 'var(--slate-a9)' }}>
                      {t('workspace.archivedChats.emptyList')}
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
