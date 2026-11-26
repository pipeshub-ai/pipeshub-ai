'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { Badge, Box, Button, Callout, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { getUserFacingErrorMessage } from '@/lib/api/api-error';
import { UsersApi } from '@/app/(main)/workspace/users/api';
import { WorkspaceRightPanel, useWorkspaceDrawerNestedModalHost } from '@/workspace/components/workspace-right-panel';
import { formatDateTime } from '@/workspace/harness-ui/format';
import { AgentsApi } from '../../api';
import type { AgentVersionPublished, AgentVersionSummary, AgentVersionsList } from '../../types';
import { hasUnpublishedChanges, nextVersionNumber, summarizeVersionDefinition, type VersionDefinitionSummary } from '../versions';
import { VersionNoteDialog } from './version-note-dialog';

type Load<T> = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; value: T };

/** What a finished rollback reports back, for the line the panel shows after it. */
export interface RollbackOutcome {
  from: number;
  result: AgentVersionPublished;
  /** The saved draft or the editor had edits chats did not run, now gone. */
  discarded: boolean;
}

function VersionDetails({ agentKey, version }: { agentKey: string; version: number }) {
  const { t, i18n } = useTranslation();
  const [open, setOpen] = useState(false);
  const [load, setLoad] = useState<Load<VersionDefinitionSummary> | null>(null);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && (load === null || load.state === 'error')) {
      setLoad({ state: 'loading' });
      AgentsApi.getAgentVersion(agentKey, version)
        .then((found) => setLoad({ state: 'ready', value: summarizeVersionDefinition(found?.definition) }))
        .catch((e: unknown) =>
          setLoad({ state: 'error', message: getUserFacingErrorMessage(e, t('agentVersions.detailsFailed')) }),
        );
    }
  };

  const number = new Intl.NumberFormat(i18n.language);
  const lines = (s: VersionDefinitionSummary): string[] => {
    const out = [
      s.models.length
        ? t('agentVersions.detailModels', { list: s.models.join(', ') })
        : t('agentVersions.detailOrgDefaultModel'),
    ];
    if (s.toolsets.length) out.push(t('agentVersions.detailTools', { list: s.toolsets.join(', ') }));
    if (s.mcpServers.length) out.push(t('agentVersions.detailMcpServers', { list: s.mcpServers.join(', ') }));
    if (s.knowledgeCount) out.push(t('agentVersions.detailKnowledge', { amount: number.format(s.knowledgeCount) }));
    if (s.skillsCount) out.push(t('agentVersions.detailSkills', { amount: number.format(s.skillsCount) }));
    if (s.instructionsLength) {
      out.push(t('agentVersions.detailInstructions', { amount: number.format(s.instructionsLength) }));
    }
    return out;
  };

  return (
    <Box mt="1">
      <Button
        type="button"
        variant="ghost"
        color="gray"
        size="1"
        aria-expanded={open}
        onClick={toggle}
        style={{ minHeight: 32 }}
      >
        <MaterialIcon name={open ? 'expand_less' : 'expand_more'} size={16} />
        {t('agentVersions.details')}
      </Button>
      {open && load ? (
        <Box mt="1" pl="2">
          {load.state === 'loading' ? (
            <Text size="1" color="gray">
              {t('agentVersions.detailsLoading')}
            </Text>
          ) : load.state === 'error' ? (
            <Text size="1" color="red">
              {load.message}
            </Text>
          ) : (
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              {lines(load.value).map((line) => (
                <li key={line}>
                  <Text size="1">{line}</Text>
                </li>
              ))}
            </ul>
          )}
        </Box>
      ) : null}
    </Box>
  );
}

function VersionRow({
  agentKey,
  item,
  live,
  who,
  canRollBack,
  onRollBack,
}: {
  agentKey: string;
  item: AgentVersionSummary;
  live: boolean;
  who: string;
  canRollBack: boolean;
  onRollBack: () => void;
}) {
  const { t, i18n } = useTranslation();
  const when = formatDateTime(item.publishedAtTimestamp, i18n.language);
  return (
    <Box
      role="listitem"
      data-testid="agent-version-row"
      data-version={item.version}
      style={{
        border: `1px solid ${live ? 'var(--accent-8)' : 'var(--olive-4)'}`,
        boxShadow: live ? 'inset 3px 0 0 var(--accent-9)' : undefined,
        borderRadius: 'var(--radius-3)',
        padding: 'var(--space-2) var(--space-3)',
        background: 'var(--color-panel-solid)',
      }}
    >
      <Flex align="center" gap="2" wrap="wrap">
        <Text size="2" weight="bold">
          {t('agentVersions.versionLabel', { version: item.version })}
        </Text>
        {live ? (
          <Badge size="1" color="green" variant="soft">
            {t('agentVersions.live')}
          </Badge>
        ) : null}
        {item.source === 'rollback' && item.rolledBackFrom ? (
          <Badge size="1" color="blue" variant="soft">
            {t('agentVersions.rolledBackFrom', { version: item.rolledBackFrom })}
          </Badge>
        ) : null}
        {item.source === 'migration' ? (
          <Badge size="1" color="gray" variant="soft">
            {t('agentVersions.beforeVersions')}
          </Badge>
        ) : null}
      </Flex>
      <Text as="p" size="1" color="gray" mt="1">
        {item.source === 'migration'
          ? t('agentVersions.migrationMeta', { when })
          : t('agentVersions.publishedMeta', { who, when })}
      </Text>
      {item.note ? (
        <Text as="p" size="2" mt="1" style={{ fontStyle: 'italic', overflowWrap: 'anywhere' }}>
          {item.note}
        </Text>
      ) : null}
      <VersionDetails agentKey={agentKey} version={item.version} />
      {canRollBack && !live ? (
        <Flex mt="1">
          <Button type="button" variant="ghost" size="1" onClick={onRollBack} style={{ minHeight: 32 }}>
            <MaterialIcon name="history" size={16} />
            {t('agentVersions.rollback')}
          </Button>
        </Flex>
      ) : null}
    </Box>
  );
}

/**
 * The agent's published versions, newest first (OWD-14): who published each and when, the live one marked, and
 * rollback for the agent's editors. A rollback resets the draft to the old version, discarding unpublished
 * changes, and publishes it as the next number; `onRolledBack` reloads the editor.
 */
export function AgentVersionsPanel({
  open,
  onOpenChange,
  agentKey,
  canEdit,
  localEdits,
  reloadKey,
  onRequestPublish,
  onRolledBack,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  agentKey: string;
  canEdit: boolean;
  /** Edits in the editor not saved yet; a rollback discards them too. */
  localEdits: boolean;
  /** Bumped by the editor after it publishes, so an open panel reloads. */
  reloadKey: number;
  onRequestPublish: () => void;
  onRolledBack: (outcome: RollbackOutcome) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [load, setLoad] = useState<Load<AgentVersionsList>>({ state: 'loading' });
  const [names, setNames] = useState<Record<string, string>>({});
  const [target, setTarget] = useState<number | null>(null);
  const [rollingBack, setRollingBack] = useState(false);
  const [rollbackError, setRollbackError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<RollbackOutcome | null>(null);
  const modalHost = useWorkspaceDrawerNestedModalHost(open);

  const reload = useCallback(async () => {
    setLoad({ state: 'loading' });
    try {
      const list = await AgentsApi.listAgentVersions(agentKey);
      setLoad({ state: 'ready', value: list });
      const ids = [...new Set(list.versions.map((v) => v.publishedBy).filter((id): id is string => Boolean(id)))];
      if (ids.length) {
        UsersApi.getUsersByIds(ids)
          .then((users) => {
            const found: Record<string, string> = {};
            for (const user of users) {
              const label = user?.name?.trim() || user?.email?.trim();
              if (user?.id && label) found[user.id] = label;
            }
            setNames(found);
          })
          .catch(() => setNames({}));
      }
    } catch (e: unknown) {
      setLoad({ state: 'error', message: getUserFacingErrorMessage(e, t('agentVersions.loadFailed')) });
    }
  }, [agentKey, t]);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload, reloadKey]);

  useEffect(() => {
    if (!open) setOutcome(null);
  }, [open]);

  const list = load.state === 'ready' ? load.value : null;
  const unpublished = hasUnpublishedChanges(list, localEdits);
  const next = nextVersionNumber(list);

  const confirmRollback = async (note: string) => {
    if (target === null) return;
    setRollingBack(true);
    setRollbackError(null);
    try {
      const result = await AgentsApi.rollbackAgentVersion(agentKey, target, note);
      const done: RollbackOutcome = {
        from: target,
        result,
        discarded: Boolean(result.discardedUnpublishedChanges) || localEdits,
      };
      await onRolledBack(done);
      setOutcome(done);
      setTarget(null);
      await reload();
    } catch (e: unknown) {
      setRollbackError(getUserFacingErrorMessage(e, t('agentVersions.rollbackFailed')));
    } finally {
      setRollingBack(false);
    }
  };

  return (
    <>
      <WorkspaceRightPanel
        open={open}
        onOpenChange={onOpenChange}
        title={t('agentVersions.panelTitle')}
        icon="history"
        iconSize={20}
        hideFooter
        style={{ width: 'min(26rem, calc(100vw - 20px))', maxWidth: 'calc(100vw - 20px)' }}
      >
        <Flex direction="column" gap="3" p="4" data-testid="agent-versions-panel">
          {outcome ? (
            <Callout.Root color="green" variant="surface" size="1" role="status" data-testid="agent-rollback-outcome">
              <Callout.Text>
                {outcome.result.created
                  ? t('agentVersions.rolledBack', { from: outcome.from, version: outcome.result.version.version })
                  : t('agentVersions.rolledBackUnchanged', {
                      from: outcome.from,
                      version: outcome.result.version.version,
                    })}
                {outcome.discarded ? ` ${t('agentVersions.discarded')}` : ''}
              </Callout.Text>
            </Callout.Root>
          ) : null}

          {list && unpublished ? (
            <Callout.Root color="amber" variant="surface" size="1" data-testid="agent-versions-unpublished">
              <Flex direction="column" gap="2" align="start">
                <Callout.Text>{t('agentVersions.unpublishedCallout')}</Callout.Text>
                {canEdit ? (
                  <Button type="button" size="1" variant="soft" color="amber" onClick={onRequestPublish} style={{ minHeight: 32 }}>
                    {next
                      ? t('agentVersions.publishAs', { version: next })
                      : t('agentVersions.publish')}
                  </Button>
                ) : null}
              </Flex>
            </Callout.Root>
          ) : null}

          {load.state === 'loading' ? (
            <Text size="2" color="gray">
              {t('agentVersions.loading')}
            </Text>
          ) : load.state === 'error' ? (
            <Callout.Root color="red" variant="surface" size="1" role="alert">
              <Flex direction="column" gap="2" align="start">
                <Callout.Text>{load.message}</Callout.Text>
                <Button type="button" size="1" variant="soft" color="gray" onClick={() => void reload()}>
                  {t('agentVersions.retry')}
                </Button>
              </Flex>
            </Callout.Root>
          ) : load.value.versions.length === 0 ? (
            <Text size="2" color="gray" data-testid="agent-versions-empty">
              {t('agentVersions.empty')}
            </Text>
          ) : (
            <Flex direction="column" gap="2" role="list" aria-label={t('agentVersions.panelTitle')}>
              {load.value.versions.map((item) => (
                <VersionRow
                  key={item.version}
                  agentKey={agentKey}
                  item={item}
                  live={item.version === load.value.latestVersion}
                  who={(item.publishedBy && names[item.publishedBy]) || t('agentVersions.unknownPublisher')}
                  canRollBack={canEdit}
                  onRollBack={() => {
                    setRollbackError(null);
                    setTarget(item.version);
                  }}
                />
              ))}
            </Flex>
          )}
        </Flex>
      </WorkspaceRightPanel>

      <VersionNoteDialog
        open={target !== null}
        onOpenChange={(o) => {
          if (!o && !rollingBack) setTarget(null);
        }}
        title={t('agentVersions.rollbackTitle', { version: target ?? '' })}
        body={
          next
            ? t('agentVersions.rollbackBody', { version: target ?? '', next })
            : t('agentVersions.rollbackBodyGeneric', { version: target ?? '' })
        }
        warning={unpublished ? t('agentVersions.rollbackDiscardWarning') : null}
        noteLabel={t('agentVersions.rollbackNoteLabel')}
        confirmLabel={t('agentVersions.rollbackConfirm')}
        busyLabel={t('agentVersions.rollingBack')}
        busy={rollingBack}
        error={rollbackError}
        onConfirm={(note) => void confirmRollback(note)}
        container={modalHost}
      />
    </>
  );
}
