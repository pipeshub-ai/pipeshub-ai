'use client';

import { Flex, Text, Tooltip } from '@radix-ui/themes';
import type React from 'react';
import { useTranslation } from 'react-i18next';
import type { Conversation } from '@/chat/types';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useNotificationStore } from '@/app/(main)/notifications/store';
import { useFeatureFlagsStore, selectCollaborativeChatsEnabled } from '@/lib/store/feature-flags-store';

/** Pure: which badges a sidebar row shows. All empty when the flag is off. */
export function chatRowBadges(
  conversation: Pick<Conversation, 'access' | 'isOwner' | 'unreadCount' | 'collaboratorCount'>,
  collabEnabled: boolean,
  muted = false,
): { role: 'read' | 'write' | null; collaborators: number; unread: number; muted: boolean } {
  if (!collabEnabled) return { role: null, collaborators: 0, unread: 0, muted: false };
  const isOwner = conversation.access?.isOwner ?? conversation.isOwner;
  const access = conversation.access;
  const role = isOwner === false && access ? (access.role === 'read' ? 'read' : 'write') : null;
  return {
    role,
    collaborators: isOwner === false ? 0 : Math.max(conversation.collaboratorCount ?? 0, 0),
    unread: Math.max(conversation.unreadCount ?? 0, 0),
    muted,
  };
}

const iconStyle = { display: 'inline-flex', alignItems: 'center', flexShrink: 0, color: 'var(--slate-11)' } as const;

function IconBadge({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Tooltip content={label}>
      <span role="img" aria-label={label} style={iconStyle}>
        {children}
      </span>
    </Tooltip>
  );
}

export function ChatRowBadges({ conversation }: { conversation: Conversation }) {
  const { t } = useTranslation();
  const collabEnabled = useFeatureFlagsStore(selectCollaborativeChatsEnabled);
  const isMuted = useNotificationStore((s) => s.mutedSessionIds.includes(conversation.id));
  const { role, collaborators, unread, muted } = chatRowBadges(conversation, collabEnabled, isMuted);
  if (!role && collaborators === 0 && unread === 0 && !muted) return null;

  return (
    <Flex align="center" gap="1" style={{ flexShrink: 0 }}>
      {role && (
        <IconBadge label={role === 'write' ? t('chat.collab.sidebar.roleWrite') : t('chat.collab.sidebar.roleRead')}>
          <MaterialIcon name={role === 'write' ? 'edit' : 'visibility'} size={16} color="var(--slate-11)" />
        </IconBadge>
      )}
      {collaborators > 0 && (
        <IconBadge label={t('chat.collab.sidebar.sharedWith', { total: collaborators })}>
          <MaterialIcon name="group" size={16} color="var(--slate-11)" />
          <Text size="1" aria-hidden style={{ marginLeft: 2, lineHeight: '16px' }}>
            {collaborators}
          </Text>
        </IconBadge>
      )}
      {muted && (
        <IconBadge label={t('chat.collab.sidebar.muted')}>
          <MaterialIcon name="notifications_off" size={16} color="var(--slate-11)" />
        </IconBadge>
      )}
      {unread > 0 && (
        <span
          role="img"
          aria-label={t('chat.collab.sidebar.unread', { total: unread })}
          title={t('chat.collab.sidebar.unread', { total: unread })}
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            backgroundColor: 'var(--accent-9)',
            flexShrink: 0,
          }}
        />
      )}
    </Flex>
  );
}
