'use client';

import React, { useCallback, useEffect, useId, useState } from 'react';
import { Badge, Box, Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { ChatApi } from '../../api';
import type { WorkspaceVersions } from '../../types';

export function versionReason(t: (key: string) => string, reason: string): string {
  switch (reason) {
    case 'autosave':
      return t('codeTaskCard.versionReason.autosave');
    case 'turn_end':
      return t('codeTaskCard.versionReason.turnEnd');
    case 'before_effect':
      return t('codeTaskCard.versionReason.beforeEffect');
    case 'rollback':
      return t('codeTaskCard.versionReason.rollback');
    case 'merge':
      return t('codeTaskCard.versionReason.merge');
    default:
      return t('codeTaskCard.versionReason.other');
  }
}

export interface WorkspaceVersionsPanelProps {
  session: { agentId?: string; sessionId: string };
}

type Status =
  | { kind: 'idle' }
  | { kind: 'restored'; from: number; to: number }
  | { kind: 'refused'; key: string };

function refusalKey(code: string): string {
  if (code.endsWith('WORKSPACE_CONFLICT')) return 'durableRun.versions.conflict';
  if (code.endsWith('WORKSPACE_WRITER_BUSY')) return 'durableRun.versions.busy';
  return 'durableRun.versions.failed';
}

/**
 * The conversation's workspace versions, newest first; any earlier one can
 * be restored as a new version on top of the current one (nothing is
 * rewritten or deleted). The server refuses a restore if the workspace moved
 * since the list was read, or while a task writes to it.
 */
export function WorkspaceVersionsPanel({ session }: WorkspaceVersionsPanelProps) {
  const { t } = useTranslation();
  const headingId = useId();
  const [list, setList] = useState<WorkspaceVersions | null>(null);
  const [failed, setFailed] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  const { agentId, sessionId } = session;
  const load = useCallback(async () => {
    try {
      setList(await ChatApi.listWorkspaceVersions({ ...(agentId ? { agentId } : {}), sessionId }));
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [agentId, sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const restore = async (revision: number) => {
    if (list === null) return;
    setBusy(true);
    const answer = await ChatApi.restoreWorkspaceVersion(session, revision, list.head);
    setBusy(false);
    setConfirming(null);
    setStatus(
      'refused' in answer
        ? { kind: 'refused', key: refusalKey(answer.refused) }
        : { kind: 'restored', from: revision, to: answer.revision },
    );
    await load();
  };

  return (
    <Box data-testid="workspace-versions">
      <Text as="div" id={headingId} size="1" weight="bold" style={{ marginBottom: 'var(--space-1)' }}>
        {t('durableRun.versions.heading')}
      </Text>
      {failed ? (
        <Text size="1" color="red">
          {t('durableRun.versions.loadFailed')}
        </Text>
      ) : list !== null && list.versions.length === 0 ? (
        <Text size="1" color="gray">
          {t('durableRun.versions.empty')}
        </Text>
      ) : null}
      {list !== null && list.versions.length > 0 ? (
        <Box asChild style={{ margin: 0, padding: 0, listStyle: 'none' }}>
          <ul aria-labelledby={headingId}>
            {list.versions.map((version) => {
              const current = version.revision === list.head;
              return (
                <li key={version.revision} data-testid="workspace-version" data-revision={version.revision}>
                  <Flex align="center" gap="2" wrap="wrap" style={{ padding: 'var(--space-1) 0' }}>
                    <Text size="1">
                      {t('durableRun.versions.item', {
                        revision: version.revision,
                        files: version.fileCount,
                        reason: versionReason(t, version.reason),
                      })}
                    </Text>
                    {version.restoredByUser ? (
                      <Text size="1" color="gray">
                        {t('durableRun.versions.byYou')}
                      </Text>
                    ) : null}
                    {current ? (
                      <Badge size="1" variant="soft" color="green" highContrast>
                        {t('durableRun.versions.current')}
                      </Badge>
                    ) : confirming === version.revision ? (
                      <Flex align="center" gap="2" role="group" aria-label={t('durableRun.versions.confirm', { revision: version.revision })}>
                        <Text size="1">{t('durableRun.versions.confirm', { revision: version.revision })}</Text>
                        <Button
                          size="1"
                          disabled={busy}
                          onClick={() => void restore(version.revision)}
                          style={{ minHeight: '28px' }}
                        >
                          {t('durableRun.versions.confirmYes')}
                        </Button>
                        <Button
                          size="1"
                          variant="soft"
                          color="gray"
                          disabled={busy}
                          onClick={() => setConfirming(null)}
                          style={{ minHeight: '28px' }}
                        >
                          {t('durableRun.versions.cancel')}
                        </Button>
                      </Flex>
                    ) : (
                      <Button
                        size="1"
                        variant="ghost"
                        onClick={() => setConfirming(version.revision)}
                        aria-label={t('durableRun.versions.restoreLabel', { revision: version.revision })}
                        style={{ minHeight: '28px' }}
                      >
                        {t('durableRun.versions.restore')}
                      </Button>
                    )}
                  </Flex>
                </li>
              );
            })}
          </ul>
        </Box>
      ) : null}
      <Text
        as="div"
        size="1"
        color={status.kind === 'refused' ? 'red' : 'gray'}
        aria-live="polite"
        data-testid="workspace-versions-status"
      >
        {status.kind === 'restored'
          ? t('durableRun.versions.restored', { from: status.from, to: status.to })
          : status.kind === 'refused'
            ? t(status.key)
            : ''}
      </Text>
    </Box>
  );
}
