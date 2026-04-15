'use client';

import React, { useMemo } from 'react';
import { Flex, Box, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { SidebarItem } from '@/chat/sidebar/sidebar-item';
import {
  CHAT_ITEM_HEIGHT,
  SIDEBAR_WIDTH,
  HEADER_HEIGHT,
  CONTENT_PADDING,
  ICON_SIZE_DEFAULT,
  KBD_BADGE_PADDING,
} from '@/app/components/sidebar';
import { getModifierSymbol } from '@/lib/utils/platform';
import type { Conversation } from '../types';

interface ArchivedChatsSidebarProps {
  conversations: Conversation[];
  isLoading: boolean;
  selectedConversationId: string | null;
  onSelect: (conversation: Conversation) => void;
  onSearchClick: () => void;
}

const KbdBadge = ({ children }: { children: React.ReactNode }) => (
  <span
    style={{
      background: 'var(--slate-1)',
      border: '1px solid var(--slate-3)',
      padding: KBD_BADGE_PADDING,
      borderRadius: 'var(--radius-2)',
      fontSize: 12,
      lineHeight: '16px',
      letterSpacing: '0.04px',
      color: 'var(--slate-12)',
      fontWeight: 400,
    }}
  >
    {children}
  </span>
);

export function ArchivedChatsSidebar({
  conversations,
  isLoading,
  selectedConversationId,
  onSelect,
  onSearchClick,
}: ArchivedChatsSidebarProps) {
  const { t } = useTranslation();
  const modKey = useMemo(() => getModifierSymbol(), []);

  return (
    <Flex
      direction="column"
      style={{
        width: `${SIDEBAR_WIDTH}px`,
        flexShrink: 0,
        height: '100%',
        borderRight: '1px solid var(--slate-6)',
        backgroundColor: 'var(--slate-1)',
        overflow: 'hidden',
        fontFamily: 'Manrope, sans-serif',
      }}
    >
      {/* Header */}
      <Box
        style={{
          height: `${HEADER_HEIGHT}px`,
          flexShrink: 0,
          display: 'flex',
          alignItems: 'center',
          padding: '0 16px',
          backgroundColor: 'var(--slate-1)',
        }}
      >
        <Text size="2" weight="medium" style={{ color: 'var(--slate-11)' }}>
          {t('workspace.archivedChats.title')}
        </Text>
      </Box>

      {/* Search Chats button */}
      <Box style={{ padding: '0 8px 8px', flexShrink: 0 }}>
        <SidebarItem
          icon={<MaterialIcon name="search" size={ICON_SIZE_DEFAULT} />}
          label={t('nav.searchChats')}
          onClick={onSearchClick}
          rightSlot={<KbdBadge>{modKey}+K</KbdBadge>}
        />
      </Box>

      {/* List */}
      <Box
        className="no-scrollbar"
        style={{ flex: 1, overflowY: 'auto', padding: CONTENT_PADDING }}
      >
        {isLoading ? (
          <Flex align="center" justify="center" style={{ paddingTop: 32 }}>
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {t('action.loading')}
            </Text>
          </Flex>
        ) : conversations.length === 0 ? (
          <Flex align="center" justify="center" style={{ paddingTop: 32 }}>
            <Text size="1" style={{ color: 'var(--slate-9)' }}>
              {t('workspace.archivedChats.emptyList')}
            </Text>
          </Flex>
        ) : (
          conversations.map((conversation) => (
            <SidebarItem
              key={conversation.id}
              isActive={selectedConversationId === conversation.id}
              onClick={() => onSelect(conversation)}
              label={
                <Text
                  size="2"
                  style={{
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                    display: 'block',
                    lineHeight: `${CHAT_ITEM_HEIGHT}px`,
                    color:
                      selectedConversationId === conversation.id
                        ? 'var(--slate-12)'
                        : 'var(--slate-11)',
                    fontWeight:
                      selectedConversationId === conversation.id ? 500 : 400,
                  }}
                >
                  {conversation.title}
                </Text>
              }
            />
          ))
        )}
      </Box>
    </Flex>
  );
}
