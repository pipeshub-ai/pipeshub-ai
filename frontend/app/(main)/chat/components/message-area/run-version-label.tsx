'use client';

import React from 'react';
import { Badge } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';

/** Which agent version answered (OWD-14), as a plain label in the answer's activity line: no ids. */
export function RunVersionLabel({ agentName, version }: { agentName: string | null; version: number | null }) {
  const { t } = useTranslation();
  if (version === null) return null;
  const name = agentName?.trim();
  return (
    <Badge size="1" color="gray" variant="soft" data-testid="run-agent-version">
      {name ? t('agentVersions.chatLabel', { name, version }) : t('agentVersions.chatLabelNoName', { version })}
    </Badge>
  );
}
