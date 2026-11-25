'use client';

import React, { useEffect, useState } from 'react';
import { Button, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { FILL_COMPOSER_COMMAND, useCommandStore } from '@/lib/store/command-store';
import { ChatApi } from '../../api';
import { startUndo } from '../../builtin-actions';
import { NEW_CHAT_KEY, useBuiltinResults } from '../../builtin-results';
import { RUN_PREFIX, sentRunLine } from '../../sandbox-commands';
import { useChatStore } from '../../store';
import { useActivityPanelStore } from '../activity-panel/activity-panel-store';
import type { DurableAgentRunHandle } from '../../types';

/** The command's id in the run's activity (UX5): its one `run_code` call, the first of the run's first turn. */
export const RUN_COMMAND_ID = 't1.0';
/** What "Ask about this" attaches of the output: its end, where results and errors are. */
export const ASK_OUTPUT_MAX_CHARS = 4_000;

/** "Exit code 3." (the run's answer) as "exit 3"; null when the command did not run. */
export function exitOf(answer: string): string | null {
  const match = /Exit code (-?\d+)\./.exec(answer);
  return match ? `exit ${match[1]}` : null;
}

/** The block "Ask about this" puts in the composer: the person chooses to send it (SD9). */
export function askBlock(line: string, answer: string, log: string): string {
  const tail = log.length > ASK_OUTPUT_MAX_CHARS ? `…${log.slice(-ASK_OUTPUT_MAX_CHARS)}` : log;
  const exit = exitOf(answer);
  return `Output of \`${RUN_PREFIX}${line}\`${exit ? ` (${exit})` : ''}:\n\`\`\`\n${tail.trimEnd()}\n\`\`\`\n\n`;
}

/** How many of the chat's latest versions are read for a /run's own (it writes at most one). */
const RUN_VERSIONS_PAGE = 50;
const changedByRun = new Map<string, Promise<boolean>>();

/** Whether the /run wrote a workspace version, read once per run; a refusal (not the owner) reads as no. */
export function runChangedFiles(run: DurableAgentRunHandle): Promise<boolean> {
  const key = `${run.sessionId}:${run.runId}`;
  const known = changedByRun.get(key);
  if (known) return known;
  const asked = ChatApi.listWorkspaceVersions(
    { ...(run.agentId ? { agentId: run.agentId } : {}), sessionId: run.sessionId },
    { limit: RUN_VERSIONS_PAGE },
  )
    .then((list) => list.versions.some((version) => version.runId === run.runId))
    .catch(() => {
      changedByRun.delete(key);
      return false;
    });
  changedByRun.set(key, asked);
  return asked;
}

/** Undo is offered only under a /run that changed files (QAFIX3 W3): one that changed none has nothing to undo. */
function useRunChangedFiles(run: DurableAgentRunHandle, isRun: boolean): boolean {
  const [changed, setChanged] = useState(false);
  useEffect(() => {
    if (!isRun) return;
    let live = true;
    void runChangedFiles(run).then((value) => {
      if (live) setChanged(value);
    });
    return () => {
      live = false;
    };
  }, [run, isRun]);
  return changed;
}

/**
 * Under a `/run` turn's answer (SLASH3 SB3b): "Ask about this" attaches its output to the next message, "Undo"
 * restores the files before it (`/undo`) when it changed any, "View full output" opens the Activity drawer at its
 * command.
 */
export function RunCommandActions({ question, answer, run }: { question: string; answer: string; run: DurableAgentRunHandle }) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const [failed, setFailed] = useState(false);
  const line = sentRunLine(question);
  const slotId = useChatStore((s) => s.activeSlotId) ?? NEW_CHAT_KEY;
  const changed = useRunChangedFiles(run, line !== null);
  if (line === null) return null;
  const height = isMobile ? 44 : 28;
  const ask = async () => {
    setFailed(false);
    try {
      const log = await ChatApi.getCommandLog(run, RUN_COMMAND_ID);
      useCommandStore.getState().dispatch(FILL_COMPOSER_COMMAND, { text: askBlock(line, answer, log) });
    } catch {
      setFailed(true);
    }
  };
  const undo = () =>
    void startUndo(
      { ...(run.agentId ? { agentId: run.agentId } : {}), sessionId: run.sessionId },
      { rowKey: slotId, afterPairs: useBuiltinResults.getState().pairs[slotId] ?? 0, typed: '/undo' },
      t,
    );
  return (
    <Flex direction="column" gap="1" data-testid="run-command-actions">
      <Flex gap="2" wrap="wrap" align="center">
        <Button size="1" variant="soft" style={{ minHeight: height }} onClick={() => void ask()}>
          {t('slashCommands.result.run.ask')}
        </Button>
        {changed ? (
          <Button size="1" variant="soft" color="gray" style={{ minHeight: height }} onClick={undo}>
            {t('slashCommands.result.run.undo')}
          </Button>
        ) : null}
        <Button
          size="1"
          variant="ghost"
          style={{ minHeight: height }}
          onClick={() => useActivityPanelStore.getState().openRun(run, isMobile, 'commands')}
        >
          {t('slashCommands.result.run.viewOutput')}
        </Button>
      </Flex>
      <Text size="1" color="gray">{t('slashCommands.result.run.notSent')}</Text>
      {failed ? <Text size="1" color="red" aria-live="polite">{t('slashCommands.result.failed')}</Text> : null}
    </Flex>
  );
}
