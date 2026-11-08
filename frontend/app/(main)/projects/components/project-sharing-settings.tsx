'use client';

import React, { useId, useState } from 'react';
import { Flex, Select, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ProjectApi } from '@/chat/project-api';
import type { ProjectChatSharing, ProjectDetail, ProjectVisibility } from '@/chat/project-types';
import { toast } from '@/lib/store/toast-store';

interface ProjectSharingSettingsProps {
  project: ProjectDetail;
  onUpdated: (project: ProjectDetail) => void;
}

/**
 * Owner-only settings shown at the top of the project's Share dialog: who can
 * find the project, and whether new chats are shared with its members by default.
 */
export function ProjectSharingSettings({ project, onUpdated }: ProjectSharingSettingsProps) {
  const { t } = useTranslation();
  const visibilityId = useId();
  const chatSharingId = useId();
  const [saving, setSaving] = useState<'visibility' | 'chatSharing' | null>(null);

  const save = async (patch: { visibility?: ProjectVisibility; chatSharing?: ProjectChatSharing }) => {
    const field = patch.visibility ? 'visibility' : 'chatSharing';
    setSaving(field);
    try {
      const updated = await ProjectApi.update(project._id, patch);
      onUpdated(updated);
      toast.success(t('chat.projects.sharingSettings.saved'));
    } catch {
      toast.error(t('chat.projects.sharingSettings.saveFailed'));
    } finally {
      setSaving(null);
    }
  };

  return (
    <Flex
      direction="column"
      gap="3"
      style={{
        padding: 'var(--space-3)',
        margin: 'var(--space-2) 0',
        border: '1px solid var(--olive-3)',
        borderRadius: 'var(--radius-2)',
        background: 'var(--olive-2)',
      }}
    >
      <Flex direction="column" gap="1">
        <Text as="label" id={visibilityId} size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('chat.projects.sharingSettings.visibilityLabel')}
        </Text>
        <Select.Root
          value={project.visibility}
          disabled={saving !== null}
          onValueChange={(value) => {
            if (value !== project.visibility) void save({ visibility: value as ProjectVisibility });
          }}
        >
          <Select.Trigger aria-labelledby={visibilityId} style={{ minHeight: 36 }} />
          <Select.Content position="popper" style={{ zIndex: 10000 }}>
            <Select.Item value="private">{t('chat.projects.sharingSettings.visibilityPrivate')}</Select.Item>
            <Select.Item value="org">{t('chat.projects.sharingSettings.visibilityOrg')}</Select.Item>
          </Select.Content>
        </Select.Root>
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {project.visibility === 'org'
            ? t('chat.projects.sharingSettings.visibilityOrgHint')
            : t('chat.projects.sharingSettings.visibilityPrivateHint')}
        </Text>
      </Flex>

      <Flex direction="column" gap="1">
        <Text as="label" id={chatSharingId} size="2" weight="medium" style={{ color: 'var(--slate-12)' }}>
          {t('chat.projects.sharingSettings.chatSharingLabel')}
        </Text>
        <Select.Root
          value={project.chatSharing}
          disabled={saving !== null}
          onValueChange={(value) => {
            if (value !== project.chatSharing) void save({ chatSharing: value as ProjectChatSharing });
          }}
        >
          <Select.Trigger aria-labelledby={chatSharingId} style={{ minHeight: 36 }} />
          <Select.Content position="popper" style={{ zIndex: 10000 }}>
            <Select.Item value="private">{t('chat.projects.sharingSettings.chatSharingPrivate')}</Select.Item>
            <Select.Item value="members">{t('chat.projects.sharingSettings.chatSharingMembers')}</Select.Item>
          </Select.Content>
        </Select.Root>
        <Text size="1" style={{ color: 'var(--slate-10)' }}>
          {project.chatSharing === 'members'
            ? t('chat.projects.sharingSettings.chatSharingMembersHint')
            : t('chat.projects.sharingSettings.chatSharingPrivateHint')}
        </Text>
      </Flex>
    </Flex>
  );
}
