'use client';

import React, { useEffect } from 'react';
import NextLink from 'next/link';
import { Box, Button, Flex, Grid, IconButton, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { useRouter } from 'next/navigation';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { RESERVED_COMMAND_NAMES } from '@/lib/commands/render';
import { SHIPPED_BUILTINS } from '../../builtin-commands';
import { STATUS_EFFORT_DEFAULT, useBuiltinResults, type BuiltinResult, type BuiltinRow } from '../../builtin-results';
import { useCommandCatalog } from '../../commands';
import { buildChatHref } from '../../build-chat-url';
import { count, formatSpend, usd } from '../../durable-run-ui';
import { builtinDescription } from '../chat-panel/slash-command-menu';
import { ViewActivityButton } from '../activity-panel/activity-panel-host';
import { FILL_COMPOSER_COMMAND, useCommandStore } from '@/lib/store/command-store';
import { confirmUndo, savedUndoRowId, startUndo } from '../../builtin-actions';
import { ChatApi } from '../../api';
import { conversationHarness } from '../../assistant-harness';
import type { MessagePair } from './message-pairs';
import { useChatStore } from '../../store';
import { FilesChanged } from './files-changed';
import { workspacePageLinks } from '@/config';
import { EngineBody, FilesBody, OpenBody, SandboxBody, UploadConfirmBody, UploadedBody } from './sandbox-result-rows';
import type { WorkspaceDiff } from '../../types';
import { ENGINE_NONE } from '../../sandbox-commands';
import { engineName } from '@/app/(main)/workspace/ai-accounts/access-copy';

function Link({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <NextLink href={href} style={{ color: 'var(--accent-11)', fontSize: 'var(--font-size-1)' }}>
      {children}
    </NextLink>
  );
}

function NextMessageNote({ running }: { running: boolean }) {
  const { t } = useTranslation();
  return running ? (
    <Text as="p" size="1" color="gray">
      {t('slashCommands.result.runKeepsPins')}
    </Text>
  ) : null;
}

function Pairs({ rows }: { rows: Array<[string, React.ReactNode]> }) {
  return (
    <Grid columns="max-content 1fr" gapX="3" gapY="1">
      {rows.map(([key, value]) => (
        <React.Fragment key={key}>
          <Text size="1" color="gray">{key}</Text>
          <Text size="1">{value}</Text>
        </React.Fragment>
      ))}
    </Grid>
  );
}

function HelpBody() {
  const { t } = useTranslation();
  const commands = useCommandCatalog((s) => s.commands).filter((c) => !RESERVED_COMMAND_NAMES.has(c.name));
  return (
    <Flex direction="column" gap="2">
      <Pairs rows={SHIPPED_BUILTINS.map((c) => [`/${c.name}`, builtinDescription(t, c)])} />
      {commands.length > 0 ? (
        <Pairs rows={commands.map((c) => [`/${c.name}`, c.description])} />
      ) : null}
      <Link href="/workspace/skills/personal">{t('slashCommands.result.manageCommands')}</Link>
    </Flex>
  );
}

/** "Agent loop", or the engine as its cards name it ("Codex"). */
export function executorLabel(t: TFunction, executor: string | null): string | null {
  if (executor === null) return null;
  if (executor === 'loop') return t('slashCommands.result.status.executorLoop');
  return t(`interaction.engine.${executor}`, { defaultValue: engineName(executor) });
}

/** "+48 −12": the line counts, colour never the only cue (the signs stay). */
function LineCounts({ additions, deletions }: { additions: number; deletions: number }) {
  return (
    <Text size="1" color="gray">
      <span style={{ color: 'var(--green-11)' }}>+{additions}</span>{' '}
      <span style={{ color: 'var(--red-11)' }}>−{deletions}</span>
    </Text>
  );
}

function ChangedFiles({ diff }: { diff: WorkspaceDiff }) {
  const { t } = useTranslation();
  return (
    <>
      <FilesChanged files={diff.files} />
      {diff.omitted > 0 ? (
        <Text size="1" color="gray">{t('slashCommands.result.diff.omitted', { count: diff.omitted })}</Text>
      ) : null}
    </>
  );
}

/** A reply is running in the row's chat: the row's buttons wait for it (O7). */
const useRowRunning = (rowKey: string): boolean => useChatStore((s) => Boolean(s.slots[rowKey]?.isStreaming));

function WaitNote({ running }: { running: boolean }) {
  const { t } = useTranslation();
  return running ? <Text size="1" color="gray">{t('slashCommands.reason.wait')}</Text> : null;
}

/** `/diff`: what the chat's workspace changed, each file's diff one click away; its buttons are `/undo` and `/review`. */
function DiffBody({ result, row, rowKey }: { result: Extract<BuiltinResult, { kind: 'diff' }>; row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  const running = useRowRunning(rowKey);
  const { diff, session } = result;
  const changed = diff.files.length + diff.omitted;
  if (changed === 0) return <Text size="1">{t('slashCommands.result.diff.none')}</Text>;
  return (
    <Flex direction="column" gap="2">
      <Flex align="center" gap="2" wrap="wrap">
        <Text size="1">{t('slashCommands.result.diff.summary', { count: changed })}</Text>
        <LineCounts additions={diff.additions} deletions={diff.deletions} />
      </Flex>
      <ChangedFiles diff={diff} />
      <Flex gap="2" wrap="wrap">
        <Button
          size="1"
          variant="soft"
          disabled={running}
          style={{ minHeight: '28px' }}
          onClick={() => void startUndo(session, { rowKey, afterPairs: row.afterPairs, typed: '/undo' }, t)}
        >
          {t('slashCommands.result.diff.undo')}
        </Button>
        <Button
          size="1"
          variant="soft"
          disabled={running}
          style={{ minHeight: '28px' }}
          onClick={() => useCommandStore.getState().dispatch(FILL_COMPOSER_COMMAND, { text: '/review ' })}
        >
          {t('slashCommands.result.diff.review')}
        </Button>
      </Flex>
      <WaitNote running={running} />
    </Flex>
  );
}

/** `/undo` before it runs: the files that go back, and a confirm. Files only (O3); the messages stay. */
function UndoConfirmBody({ result, row, rowKey }: { result: Extract<BuiltinResult, { kind: 'undoConfirm' }>; row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  const running = useRowRunning(rowKey);
  const dismiss = useBuiltinResults((s) => s.dismiss);
  const { diff } = result;
  return (
    <Flex direction="column" gap="2">
      <Flex align="center" gap="2" wrap="wrap">
        <Text size="1">{t('slashCommands.result.undo.confirm', { count: diff.files.length + diff.omitted })}</Text>
        <LineCounts additions={diff.additions} deletions={diff.deletions} />
      </Flex>
      <ChangedFiles diff={diff} />
      <Text size="1" color="gray">{t('slashCommands.result.undo.filesOnly')}</Text>
      <Flex gap="2" wrap="wrap" role="group" aria-label={t('slashCommands.result.undo.confirmLabel')}>
        <Button size="1" color="red" disabled={running || result.busy === true} style={{ minHeight: '28px' }} onClick={() => void confirmUndo(rowKey, row.id)}>
          {t('slashCommands.result.undo.restore')}
        </Button>
        <Button size="1" variant="soft" color="gray" disabled={result.busy === true} style={{ minHeight: '28px' }} onClick={() => dismiss(rowKey, row.id)}>
          {t('durableRun.versions.cancel')}
        </Button>
      </Flex>
      <WaitNote running={running} />
      <Text size="1" color="red" aria-live="polite">
        {result.refused ? t(result.refused) : ''}
      </Text>
    </Flex>
  );
}

function ResultBody({ result, row, rowKey }: { result: BuiltinResult; row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  const router = useRouter();
  const isMobile = useIsMobile();
  const none = t('slashCommands.result.none');
  switch (result.kind) {
    case 'loading':
      return <Text size="1" color="gray">{t('slashCommands.result.loading')}</Text>;
    case 'failed':
      return <Text size="1" color="red">{t('slashCommands.result.failed')}</Text>;
    case 'note':
      return <Text size="1">{result.text}</Text>;
    case 'help':
      return <HelpBody />;
    case 'model':
      return (
        <Flex direction="column" gap="1">
          <Text size="1">
            {t('slashCommands.result.modelNext', { model: result.model })}
            {result.was && result.was !== result.model ? (
              <Text color="gray"> {t('slashCommands.result.was', { model: result.was })}</Text>
            ) : null}
          </Text>
          <NextMessageNote running={result.running} />
        </Flex>
      );
    case 'effort':
      return (
        <Flex direction="column" gap="1">
          <Text size="1">{t('slashCommands.result.effortNext', { effort: result.effort })}</Text>
          <NextMessageNote running={result.running} />
        </Flex>
      );
    case 'compact':
      return (
        <Flex direction="column" gap="1">
          <Text size="1">{t('slashCommands.builtin.compact.requested')}</Text>
          {result.focus ? (
            <Text size="1" color="gray">{t('slashCommands.builtin.compact.requestedFocus', { focus: result.focus })}</Text>
          ) : null}
        </Flex>
      );
    case 'plan':
      return (
        <Flex direction="column" gap="1">
          <Text size="1">{t('slashCommands.result.planNext')}</Text>
          <NextMessageNote running={result.running} />
        </Flex>
      );
    case 'status': {
      const s = result.status;
      const rows: Array<[string, React.ReactNode]> = [[t('slashCommands.result.status.model'), s.model ?? none]];
      if (s.legacy) {
        rows.push([t('slashCommands.result.status.mode'), s.mode ?? none]);
      } else {
        // What the run did not set reads as what applies ("Model default", "None asked"); a dash only when unknown.
        const asked = s.approvalsAsked === null ? none
          : s.approvalsAsked === 0 ? t('slashCommands.result.status.approvalsNone')
          : t('slashCommands.result.status.approvalsAsked', { count: s.approvalsAsked });
        rows.push(
          [t('slashCommands.result.status.effort'), s.effort === STATUS_EFFORT_DEFAULT ? t('slashCommands.result.status.effortDefault') : (s.effort ?? none)],
          [t('slashCommands.result.status.executor'), executorLabel(t, s.executor) ?? none],
          ...(s.codingEngine !== null
            ? [[t('slashCommands.result.status.codingEngine'), s.codingEngine === ENGINE_NONE ? t('slashCommands.result.engine.noEngine') : engineName(s.codingEngine)] as [string, React.ReactNode]]
            : []),
          [t('slashCommands.result.status.tools'), s.tools === null ? none : count(s.tools)],
          [t('slashCommands.result.status.approvals'), s.approvals ?? asked],
          [t('slashCommands.result.status.run'), s.runStatus ?? none],
          [t('slashCommands.result.status.tokens'), s.tokens === null ? none : count(s.tokens)],
          ...(s.policyVersion !== null
            ? [[t('slashCommands.result.status.policyVersion'), s.policyVersion] as [string, React.ReactNode]]
            : []),
        );
      }
      return (
        <Flex direction="column" gap="2">
          <Pairs rows={rows} />
          {s.legacy ? <Text size="1" color="gray">{t('slashCommands.result.status.legacy')}</Text> : null}
          {result.run ? <Box><ViewActivityButton run={result.run} isMobile={isMobile} /></Box> : null}
        </Flex>
      );
    }
    case 'usage': {
      const { chat, last } = result.usage;
      const lastSpend = last ? formatSpend(last) : null;
      const capLeft = last?.available.usdMicros ?? null;
      return (
        <Flex direction="column" gap="2">
          <Pairs
            rows={[
              [
                t('slashCommands.result.usage.chat'),
                chat
                  ? t('slashCommands.result.usage.chatValue', { tokens: count(chat.tokens), usd: usd(chat.usdMicros), runs: chat.runs })
                  : none,
              ],
              [
                t('slashCommands.result.usage.last'),
                lastSpend
                  ? capLeft === null
                    ? t('slashCommands.result.usage.lastValue', { tokens: lastSpend.tokens, usd: lastSpend.spent })
                    : t('slashCommands.result.usage.lastValueCap', { tokens: lastSpend.tokens, usd: lastSpend.spent, left: usd(capLeft) })
                  : none,
              ],
            ]}
          />
          {result.run ? <Box><ViewActivityButton run={result.run} isMobile={isMobile} /></Box> : null}
        </Flex>
      );
    }
    case 'list': {
      const page =
        result.list === 'skills'
          ? { href: '/workspace/skills/personal', label: t('slashCommands.result.openSkills'), empty: t('slashCommands.result.noSkills') }
          : result.list === 'mcp'
            ? { href: '/workspace/mcp-servers/personal', label: t('slashCommands.result.openMcp'), empty: t('slashCommands.result.noMcp') }
            : { href: '/agents', label: t('slashCommands.result.openAgents'), empty: t('slashCommands.result.noAgents') };
      return (
        <Flex direction="column" gap="2">
          {result.items.length === 0 ? <Text size="1" color="gray">{page.empty}</Text> : null}
          {result.list === 'agents' ? (
            <Flex direction="column" gap="1" align="start">
              {result.items.map((item) => (
                <Button
                  key={item.id}
                  size="1"
                  variant="ghost"
                  onClick={() => router.push(buildChatHref({ agentId: item.id }))}
                >
                  {item.name}
                </Button>
              ))}
            </Flex>
          ) : (
            <Pairs rows={result.items.map((item) => [item.name, item.detail ?? ''])} />
          )}
          <Link href={page.href}>{page.label}</Link>
        </Flex>
      );
    }
    case 'diff':
      return <DiffBody result={result} row={row} rowKey={rowKey} />;
    case 'undoConfirm':
      return <UndoConfirmBody result={result} row={row} rowKey={rowKey} />;
    case 'files':
      return <FilesBody result={result} row={row} rowKey={rowKey} />;
    case 'open':
      return <OpenBody result={result} row={row} rowKey={rowKey} />;
    case 'sandbox':
      return <SandboxBody result={result} row={row} rowKey={rowKey} />;
    case 'uploadConfirm':
      return <UploadConfirmBody result={result} row={row} rowKey={rowKey} />;
    case 'uploaded':
      return <UploadedBody result={result} row={row} rowKey={rowKey} />;
    case 'engine':
      return <EngineBody result={result} row={row} rowKey={rowKey} />;
    case 'uploadRefused':
      return <Text size="1" color="red">{t(result.key)}</Text>;
    case 'engineNotSaved':
      return <Text size="1">{t('slashCommands.result.engine.refused')}</Text>;
    case 'undone':
      return (
        <Flex direction="column" gap="1">
          <Text size="1">
            {result.files === undefined || result.restoredFrom === undefined
              ? t('slashCommands.result.undo.doneSaved', { revision: result.revision })
              : t('slashCommands.result.undo.done', { count: result.files, from: result.restoredFrom, revision: result.revision })}
          </Text>
          <Text size="1" color="gray">{t('slashCommands.result.undo.forkHint')}</Text>
        </Flex>
      );
    case 'permissions': {
      const p = result.permissions;
      const policyPage = workspacePageLinks['approval-policy'];
      const yes = t('slashCommands.result.yes');
      const no = t('slashCommands.result.no');
      return (
        <Flex direction="column" gap="2">
          <Pairs
            rows={[
              [t('slashCommands.result.permissions.approve'), p.approve ? yes : no],
              [t('slashCommands.result.permissions.deny'), p.deny ? yes : no],
              ...Object.entries(p.approvals ?? {}).map(([effect, mode]): [string, React.ReactNode] => [effect, mode]),
            ]}
          />
          <Text size="1" color="gray">{t('slashCommands.result.permissions.readOnly')}</Text>
          {result.admin && policyPage !== undefined ? (
            <Link href={policyPage}>{t('slashCommands.result.permissions.policy')}</Link>
          ) : null}
        </Flex>
      );
    }
    default:
      return null;
  }
}

/** One built-in's result: a compact row in the timeline, styled as a status row, never a chat bubble (§3). */
export function BuiltinResultRow({ row, rowKey }: { row: BuiltinRow; rowKey: string }) {
  const { t } = useTranslation();
  const dismiss = useBuiltinResults((s) => s.dismiss);
  return (
    <Box
      role="status"
      data-testid="builtin-result-row"
      data-builtin-row-id={row.id}
      style={{ borderLeft: '2px solid var(--gray-a5)', padding: 'var(--space-1) 0 var(--space-1) var(--space-3)', margin: 'var(--space-3) 0' }}
    >
      <Flex align="center" justify="between" gap="2">
        <Text size="1" color="gray" style={{ fontFamily: 'monospace' }}>
          {row.typed}
        </Text>
        <IconButton
          size="1"
          variant="ghost"
          color="gray"
          aria-label={t('slashCommands.result.dismiss')}
          onClick={() => dismiss(rowKey, row.id)}
        >
          <MaterialIcon name="close" size={14} />
        </IconButton>
      </Flex>
      <Box style={{ marginTop: 'var(--space-1)' }}>
        <ResultBody result={row.result} row={row} rowKey={rowKey} />
      </Box>
    </Box>
  );
}

/** The rows typed after `afterPairs` question/answer pairs; `last` also takes rows typed after more pairs than are shown. */
export function BuiltinResultRows({ rows, rowKey, afterPairs, last = false }: { rows: BuiltinRow[]; rowKey: string; afterPairs: number; last?: boolean }) {
  const shown = rows.filter((row) => row.afterPairs === afterPairs || (last && row.afterPairs > afterPairs));
  if (shown.length === 0) return null;
  return (
    <>
      {shown.map((row) => (
        <BuiltinResultRow key={row.id} row={row} rowKey={rowKey} />
      ))}
    </>
  );
}

/** One saved `/upload` row per `upload` revision (the live row takes the same id). */
export const savedUploadRowId = (revision: number): string => `upload-revision-${String(revision)}`;

/** How many saved restores a chat reads back on open. */
const SAVED_UNDO_PAGE = 50;

/**
 * A person's restore is saved as the workspace's `rollback` revision (O6): on open, a new-harness chat reads its
 * versions once and shows each restore as a row after the reply it followed. Owner only; anyone else sees none.
 */
export function useSavedUndoRows(rowKey: string, pairs: MessagePair[]): void {
  const { t } = useTranslation();
  const slot = useChatStore((s) => s.slots[rowKey]);
  const convId = slot?.convId ?? null;
  const agentId = slot?.threadAgentId ?? undefined;
  const durable = conversationHarness(slot) === 'durable';
  // The workspace's versions are its owner's (D14): a shared chat's viewer is not asked to be refused.
  const owner = slot?.isOwner === true;
  const loaded = pairs.length > 0;
  const pairsRef = React.useRef(pairs);
  useEffect(() => {
    pairsRef.current = pairs;
  });
  useEffect(() => {
    if (!convId || !durable || !loaded || !owner) return;
    let live = true;
    void ChatApi.listWorkspaceVersions({ ...(agentId ? { agentId } : {}), sessionId: convId }, { limit: SAVED_UNDO_PAGE })
      .then((list) => {
        if (!live) return;
        const sent = pairsRef.current.map((p) => (p.createdAt ? Date.parse(p.createdAt) : Number.NaN));
        for (const version of list.versions) {
          // A person's upload is saved as its own `upload` revision (SLASH3 SD6).
          if (version.reason === 'upload') {
            const id = savedUploadRowId(version.revision);
            // The row this browser showed for it keeps its file list.
            if (useBuiltinResults.getState().rows[rowKey]?.some((row) => row.id === id)) continue;
            useBuiltinResults.getState().put(rowKey, {
              id,
              typed: t('slashCommands.result.upload.savedHeading'),
              afterPairs: sent.filter((at) => at <= version.createdAt).length,
              result: {
                kind: 'uploaded',
                revision: version.revision,
                ...(version.paths ? { paths: version.paths } : {}),
                // Undo is `/undo`, which takes back the last change: offered only while that is still this upload.
                ...(version.revision === list.head ? { session: { ...(agentId ? { agentId } : {}), sessionId: convId } } : {}),
              },
            });
            continue;
          }
          if (!version.restoredByUser || version.reason !== 'rollback') continue;
          useBuiltinResults.getState().put(rowKey, {
            id: savedUndoRowId(version.revision),
            typed: t('slashCommands.result.undo.savedHeading'),
            afterPairs: sent.filter((at) => at <= version.createdAt).length,
            result: { kind: 'undone', revision: version.revision },
          });
        }
      })
      .catch(() => {
        // Not the owner, or no workspace: nothing to show.
      });
    return () => {
      live = false;
    };
  }, [rowKey, convId, agentId, durable, loaded, owner, t]);
}
