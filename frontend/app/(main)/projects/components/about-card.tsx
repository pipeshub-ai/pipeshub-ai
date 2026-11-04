'use client';

import React, { useEffect, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { ShareCommonApi } from '@/app/components/share/api';
import { SharedAvatarStack } from '@/app/components/share/shared-avatar-stack';
import type { TFunction } from 'i18next';
import { useUserStore } from '@/lib/store/user-store';
import type { ProjectDetail } from '@/chat/project-types';
import { PanelCard, PanelHeader, PanelRow } from './panel-section';

interface AboutCardProps {
  project: ProjectDetail;
  isOwner: boolean;
  /** Opens the Share dialog from the Members row (owner only). */
  onOpenShare?: () => void;
}

/** Who can see the project: the whole org, the people it is shared with, or only its owner. */
export function projectVisibilityLabel(
  project: Pick<ProjectDetail, 'visibility' | 'members'>,
  isOwner: boolean,
  t: TFunction,
): string {
  if (project.visibility === 'org') return t('chat.projects.workspace.visibilityOrg');
  const count = project.members?.length ?? 0;
  if (count > 0) {
    return t('chat.projects.workspace.visibilityShared', {
      members: t('chat.projects.workspace.memberCount', { count }),
    });
  }
  return isOwner ? t('chat.projects.workspace.visibilityPrivate') : t('chat.projects.workspace.visibilityPrivateOwner');
}

export function AboutCard({ project, isOwner, onOpenShare }: AboutCardProps) {
  const { t } = useTranslation();
  const profile = useUserStore((s) => s.profile);
  const [owner, setOwner] = useState<{ name: string; email?: string; avatarUrl?: string } | null>(null);

  useEffect(() => {
    if (isOwner) return;
    let cancelled = false;
    ShareCommonApi.getUsersByIds([project.userId])
      .then((users) => {
        if (cancelled) return;
        const u = users[0];
        setOwner(u ? { name: u.name, email: u.email, avatarUrl: u.avatarUrl } : null);
      })
      .catch(() => {
        if (!cancelled) setOwner(null);
      });
    return () => {
      cancelled = true;
    };
  }, [isOwner, project.userId]);

  const ownerAvatar = isOwner ? (
    <UserAvatar
      fullName={profile?.fullName}
      firstName={profile?.firstName}
      lastName={profile?.lastName}
      email={profile?.email}
      src={profile?.avatarUrl}
      size={20}
    />
  ) : (
    <UserAvatar fullName={owner?.name} email={owner?.email} src={owner?.avatarUrl} size={20} />
  );
  const ownerName = isOwner
    ? t('chat.projects.workspace.youLabel', { defaultValue: 'You' })
    : owner?.name?.trim() || owner?.email || t('chat.projects.roles.owner');

  const visibilityLabel = projectVisibilityLabel(project, isOwner, t);
  const memberCount = project.members?.length ?? 0;
  const avatarMembers = (project.members ?? []).map((m) => ({
    id: `${m.principalType}:${m.principalId}`,
    name: m.name || m.email || '',
    type: m.principalType,
  }));

  return (
    <PanelCard>
      <PanelHeader title={t('chat.projects.workspace.aboutTitle', { defaultValue: 'About' })} />
      <Box style={{ padding: '0 var(--space-4)' }}>
        <PanelRow
          label={t('chat.projects.workspace.ownerLabel', { defaultValue: 'Owner' })}
          value={
            <Flex align="center" gap="2">
              {ownerAvatar}
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                {ownerName}
              </Text>
            </Flex>
          }
          isFirst
        />
        <PanelRow
          label={t('chat.projects.workspace.visibilityLabel', { defaultValue: 'Visibility' })}
          value={visibilityLabel}
        />
        <PanelRow
          label={t('chat.projects.workspace.membersTitle')}
          onClick={isOwner ? onOpenShare : undefined}
          value={
            <Flex align="center" gap="2">
              {memberCount > 0 && <SharedAvatarStack members={avatarMembers} maxVisible={3} size={20} />}
              <Text size="1" style={{ color: 'var(--slate-9)' }}>
                {memberCount > 0
                  ? t('chat.projects.workspace.memberCount', { count: memberCount })
                  : t('chat.projects.workspace.membersStatusOnlyYou')}
              </Text>
            </Flex>
          }
        />
      </Box>
    </PanelCard>
  );
}
