'use client';

import React, { useEffect, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { UserAvatar } from '@/app/components/ui/user-avatar';
import { ShareCommonApi } from '@/app/components/share/api';
import { useUserStore } from '@/lib/store/user-store';
import type { ProjectDetail } from '@/chat/project-types';
import { PanelCard, PanelHeader, PanelRow } from './panel-section';

interface AboutCardProps {
  project: ProjectDetail;
  isOwner: boolean;
}

export function AboutCard({ project, isOwner }: AboutCardProps) {
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

  const visibilityLabel =
    project.visibility === 'org'
      ? t('chat.projects.workspace.visibilityOrg', { defaultValue: 'Visible to your organization' })
      : t('chat.projects.workspace.visibilityPrivate', { defaultValue: 'Private to you' });

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
      </Box>
    </PanelCard>
  );
}
