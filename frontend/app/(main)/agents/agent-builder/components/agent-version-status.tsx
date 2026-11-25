'use client';

import React from 'react';
import { Badge, Flex } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { AgentVersioning } from '../../types';

const dot = (
  <span
    aria-hidden
    style={{ width: 6, height: 6, borderRadius: '50%', background: 'currentColor', display: 'inline-block' }}
  />
);

/**
 * Where the agent stands against its published versions (OWD-14): the version chats run, and whether there are
 * edits they do not run yet. A new agent has none until it is created.
 */
export function AgentVersionStatus({
  editing,
  versioning,
  unpublished,
}: {
  editing: boolean;
  versioning: AgentVersioning | null | undefined;
  unpublished: boolean;
}) {
  const { t } = useTranslation();
  if (!editing) {
    return (
      <Badge size="1" color="gray" variant="soft" data-testid="agent-version-status" style={{ gap: 6 }}>
        {dot}
        {t('agentVersions.notPublishedYet')}
      </Badge>
    );
  }
  if (!versioning) return null;
  return (
    <Flex align="center" gap="2" wrap="wrap" data-testid="agent-version-status">
      <Badge size="1" color="green" variant="soft" style={{ gap: 6 }}>
        {dot}
        {t('agentVersions.liveVersion', { version: versioning.latestVersion })}
      </Badge>
      {unpublished ? (
        <Badge size="1" color="amber" variant="soft" style={{ gap: 6 }}>
          {dot}
          {t('agentVersions.unpublishedChanges')}
        </Badge>
      ) : null}
    </Flex>
  );
}
