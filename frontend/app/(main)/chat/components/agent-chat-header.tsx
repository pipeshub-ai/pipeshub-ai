'use client';

import React from 'react';
import { useRouter } from 'next/navigation';
import { Box, DropdownMenu, Text, Tooltip } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { IconMenuItem } from '@/app/components/ui/icon-menu-item';
import { useChatStore } from '@/chat/store';
import { useMobileSidebarStore } from '@/lib/store/mobile-sidebar-store';
import { openFreshAgentChat } from '@/chat/build-chat-url';

interface AgentChatHeaderProps {
  agentId: string;
  displayName: string | null;
  isMobile: boolean;
  /** When true the desktop left offset is increased to make room for the sidebar expand button. */
  hasExpandButton?: boolean;
  /** A chat someone shared with the user: they cannot start chats on an agent they may not have. */
  readOnly?: boolean;
  /** Share controls sit top-right; the mobile title stops short of them. */
  hasShareGroup?: boolean;
}

/** Room the top-right share group (avatars + Share button + 16px inset) takes on a phone. */
const MOBILE_SHARE_GROUP_PX = 200;

/**
 * Top-left chat title for agent threads — name + chevron menu (ChatGPT-style).
 */
export function AgentChatHeader({
  agentId,
  displayName,
  isMobile,
  hasExpandButton,
  hasShareGroup,
  readOnly,
}: AgentChatHeaderProps) {
  const router = useRouter();
  const { t } = useTranslation();
  const access = useChatStore((s) => s.agentContextAccess);
  const closeMobileSidebar = useMobileSidebarStore((s) => s.close);

  // The name only loads for someone allowed to open the agent; for a recipient it never will.
  const sharedWithoutAgent = Boolean(readOnly) && !displayName?.trim();
  const title = displayName?.trim() || (readOnly ? t('chat.sharedAgentChat') : t('chat.agentNameLoading'));
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
    router.push('/chat/');
  };

  const handleBrowseAgents = () => {
    if (isMobile) closeMobileSidebar();
    router.push('/agents/');
  };

  const handleOpenBuilder = () => {
    if (isMobile) closeMobileSidebar();
    router.push(`/agents/edit?agentKey=${encodeURIComponent(agentId)}`);
  };

  return (
    <Box
      style={{
        position: 'absolute',
        top: 12,
        left: isMobile ? 52 : (hasExpandButton ? 52 : 16),
        zIndex: 25,
        maxWidth: isMobile
          ? `calc(100vw - 52px - ${hasShareGroup ? MOBILE_SHARE_GROUP_PX : 16}px)`
          : 'min(480px, calc(100vw - 280px))',
        pointerEvents: 'auto',
      }}
    >
      {sharedWithoutAgent ? (
        <Text size="3" weight="medium" style={{ padding: 'var(--space-1) var(--space-2)', color: 'var(--slate-12)' }}>
          {title}
        </Text>
      ) : (
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
              padding: 'var(--space-1) var(--space-2)',
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
                lineHeight: 'var(--line-height-3)',
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
          <IconMenuItem icon="assistant" onSelect={handleNewAgentChat}>
            {t('chat.agentMenuNewChat')}
          </IconMenuItem>
          <IconMenuItem icon="forum" onSelect={handleAllChats}>
            {t('chat.agentMenuAllChats')}
          </IconMenuItem>
          <IconMenuItem icon="smart_toy" onSelect={handleBrowseAgents}>
            {t('chat.moreAgents')}
          </IconMenuItem>
          {(canEdit || showViewAgent) && <DropdownMenu.Separator />}
          {canEdit && (
            <IconMenuItem icon="edit" onSelect={handleOpenBuilder}>
              {t('chat.editAgent')}
            </IconMenuItem>
          )}
          {showViewAgent && (
            <IconMenuItem icon="visibility" textValue={t('chat.viewAgent')} onSelect={handleOpenBuilder}>
              <Tooltip content={viewTooltip} delayDuration={400}>
                <span>{t('chat.viewAgent')}</span>
              </Tooltip>
            </IconMenuItem>
          )}
        </DropdownMenu.Content>
      </DropdownMenu.Root>
      )}
    </Box>
  );
}
