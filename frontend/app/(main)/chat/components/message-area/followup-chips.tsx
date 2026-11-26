'use client';

import React, { useId, useMemo } from 'react';
import { Button, Flex, Text, VisuallyHidden } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import { MaterialIcon } from '@/app/components/ui/MaterialIcon';
import { useIsMobile } from '@/lib/hooks/use-is-mobile';
import { FILL_COMPOSER_COMMAND, useCommandStore } from '@/lib/store/command-store';
import { engineActionChips, engineSummaryFromTimeline, toFollowups } from '../../activity';
import { useRunFollowups } from '../../hooks/use-run-followups';
import { useRunTimeline } from '../../hooks/use-run-timeline';
import { useChatStore } from '../../store';
import { useActivityPanelStore } from '../activity-panel/activity-panel-store';
import { useActivityRun } from '../activity-panel/use-activity-run';

function fillComposer(text: string): void {
  useCommandStore.getState().dispatch(FILL_COMPOSER_COMMAND, { text });
}

function Chip({ icon, label, describedBy, onClick }: { icon: string; label: string; describedBy?: string; onClick: () => void }) {
  return (
    <Button
      variant="outline"
      color="gray"
      size="2"
      radius="full"
      onClick={onClick}
      aria-describedby={describedBy}
      data-testid="followup-chip"
      style={{ height: 'auto', minHeight: 32, padding: 'var(--space-1) var(--space-3)', whiteSpace: 'normal', textAlign: 'left', cursor: 'pointer' }}
    >
      <span aria-hidden="true" style={{ display: 'inline-flex' }}>
        <MaterialIcon name={icon} size={16} color="var(--gray-11)" />
      </span>
      {label}
    </Button>
  );
}

/**
 * Suggested next steps under the latest answer (UX5 D-6): the run's own
 * follow-up questions and, for an engine run, actions from what it did.
 * Rendered by the message list for the latest completed answer only. The
 * questions arrive after the run ended, so they may appear a little later.
 */
export function FollowupChips() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const hintId = useId();
  const isStreaming = useChatStore((s) => (s.activeSlotId ? (s.slots[s.activeSlotId]?.isStreaming ?? false) : false));
  const run = useActivityRun();
  const justFinished = useChatStore((s) => {
    const followed = s.activeSlotId ? s.slots[s.activeSlotId]?.lastDurableRun : null;
    return Boolean(run && followed && followed.runId === run.runId);
  });
  const openSection = useActivityPanelStore((s) => s.openSection);
  const session = useMemo(
    () => (run ? { ...(run.agentId ? { agentId: run.agentId } : {}), sessionId: run.sessionId } : null),
    [run],
  );
  const ready = !isStreaming && run !== null;
  const { timeline } = useRunTimeline(session, run?.runId, { enabled: ready });
  const offered = useRunFollowups(run, { enabled: ready, justFinished });

  const current = timeline && run && timeline.runId === run.runId ? timeline : null;
  const followups = toFollowups([...offered, ...(current?.followups ?? [])]).map(({ text }) => text);
  const actions = current ? engineActionChips(engineSummaryFromTimeline(current)) : [];

  if (isStreaming || (!followups.length && !actions.length)) return null;

  return (
    <Flex direction="column" gap="2" style={{ marginTop: 'var(--space-4)' }} data-testid="followup-chips">
      <Text size="1" color="gray" weight="medium">
        {t('followups.title')}
      </Text>
      <VisuallyHidden id={hintId}>{t('followups.hint')}</VisuallyHidden>
      <Flex role="group" aria-label={t('followups.title')} gap="2" wrap="wrap">
        {followups.map((text) => (
          <Chip key={text} icon="chat_bubble_outline" label={text} describedBy={hintId} onClick={() => fillComposer(text)} />
        ))}
        {actions.map((action) =>
          action.kind === 'openFiles' ? (
            <Chip
              key="openFiles"
              icon="difference"
              label={t('followups.openFiles')}
              onClick={() => {
                useChatStore.getState().clearPreview();
                openSection('files', isMobile);
              }}
            />
          ) : (
            <Chip
              key="runTests"
              icon="science"
              label={t('followups.runTests')}
              describedBy={hintId}
              onClick={() => fillComposer(t('followups.runTestsPrompt', { command: action.command }))}
            />
          ),
        )}
      </Flex>
    </Flex>
  );
}
