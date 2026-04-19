'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { Box, DropdownMenu, Flex, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useChatStore } from '@/chat/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { openFreshAgentChat } from '@/chat/build-chat-url';

interface AgentChatHeaderProps {
  agentId: string;
  displayName: string | null;
  isMobile: boolean;
}

/**
 * Top-left chat title for agent threads — name + chevron menu (ChatGPT-style).
 */
export function AgentChatHeader({ agentId, displayName, isMobile }: AgentChatHeaderProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const toggleAgentsSidebar = useChatStore((s) => s.toggleAgentsSidebar);
  const closeAgentsSidebar = useChatStore((s) => s.closeAgentsSidebar);
  const access = useChatStore((s) => s.agentContextAccess);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);
  const openMobileSidebar = useMobileSidebarStore((s) => s.open);

  const title = displayName?.trim() || t('chat.agentNameLoading');
  const canEdit = Boolean(access?.canEdit);
  const showViewAgent = Boolean(access?.showViewAgent);
  const viewTooltip = showViewAgent
    ? access?.viewAgentTooltipVariant === 'service_account'
      ? t('chat.viewAgentTooltipServiceAccount')
      : t('chat.viewAgentTooltipIndividual')
    : '';

  const handleNewAgentChat = () => {
    if (isMobile) closeMobileSidebar();
    openFreshAgentChat(agentId, router);
  };

  const handleAllChats = () => {
    if (isMobile) closeMobileSidebar();
    closeAgentsSidebar();
    router.push('/chat/');
  };

  const handleBrowseAgents = () => {
    toggleAgentsSidebar();
    if (isMobile) openMobileSidebar();
  };

  const handleOpenBuilder = () => {
    if (isMobile) closeMobileSidebar();
    closeAgentsSidebar();
    router.push(`/agents/edit?agentKey=${encodeURIComponent(agentId)}`);
  };

  return (
    <Box
      style={{
        position: 'absolute',
        top: 12,
        left: isMobile ? 52 : 16,
        zIndex: 25,
        maxWidth: isMobile ? 'calc(100vw - 120px)' : 'min(480px, calc(100vw - 280px))',
        pointerEvents: 'auto',
      }}
    >
      <DropdownMenu.Root>
        <DropdownMenu.Trigger>
          <button
            type="button"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 2,
              maxWidth: '100%',
              margin: 0,
              padding: '6px 10px',
              background: 'transparent',
              border: 'none',
              borderRadius: 'var(--radius-2)',
              cursor: 'pointer',
              fontFamily: 'var(--default-font-family, Manrope, sans-serif)',
            }}
            aria-label={t('chat.agentHeaderMenuAria')}
          >
            <span
              style={{
                fontSize: 15,
                fontWeight: 500,
                lineHeight: '22px',
                color: 'var(--slate-12)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                textAlign: 'left',
              }}
            >
              {title}
            </span>
            <MaterialIcon name="expand_more" size={22} color="var(--slate-11)" />
          </button>
        </DropdownMenu.Trigger>
        <DropdownMenu.Content align="start" size="2">
          <DropdownMenu.Item onSelect={handleNewAgentChat}>
            {t('chat.agentMenuNewChat')}
          </DropdownMenu.Item>
          <DropdownMenu.Item onSelect={handleAllChats}>
            {t('chat.agentMenuAllChats')}
          </DropdownMenu.Item>
          <DropdownMenu.Item onSelect={handleBrowseAgents}>
            {t('chat.moreAgents')}
          </DropdownMenu.Item>
          {(canEdit || showViewAgent) && <DropdownMenu.Separator />}
          {canEdit && (
            <DropdownMenu.Item onSelect={handleOpenBuilder}>
              <Flex align="center" gap="2">
                <MaterialIcon name="edit" size={16} color="var(--slate-11)" />
                <Text size="2">{t('chat.editAgent')}</Text>
              </Flex>
            </DropdownMenu.Item>
          )}
          {showViewAgent && (
            <DropdownMenu.Item onSelect={handleOpenBuilder}>
              <Tooltip content={viewTooltip} delayDuration={400}>
                <Flex align="center" gap="2" style={{ maxWidth: 280 }}>
                  <MaterialIcon name="visibility" size={16} color="var(--slate-11)" />
                  <Text size="2">{t('chat.viewAgent')}</Text>
                </Flex>
              </Tooltip>
            </DropdownMenu.Item>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Root>
    </Box>
  );
}
