'use client';

import React, { useContext, useEffect, useState } from 'react';
import { Box, Flex, Text } from '@radix-ui/themes';
import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';
import { count, usd } from '../../durable-run-ui';
import { useRunTimeline } from '../../hooks/use-run-timeline';
import type { DurableAgentRunHandle, RunDigestView } from '../../types';
import { ActivitySectionOpen } from './agent-activity';

/** How many saved files the summary names before it counts the rest. */
const NAMED_FILES = 3;

/**
 * The end-of-run summary (G-07) as plain lines: how it ended, what it used, read, changed and saved, the
 * approvals it asked and what it cost. Counts and file names only; no ids, no raw values.
 */
export function runSummaryLines(digest: RunDigestView, t: TFunction): string[] {
  const lines: string[] = [];
  const outcome = t(`agentActivity.runSummary.outcome.${digest.outcome}`);
  lines.push(digest.cutShortBy ? `${outcome} · ${t(`agentActivity.runSummary.cutShort.${digest.cutShortBy}`)}` : outcome);
  const { actions, approvals, records } = digest;
  if (actions.total > 0) {
    const used = [t('agentActivity.summary.tools', { count: actions.total })];
    if (actions.failed > 0) used.push(t('agentActivity.runSummary.failed', { count: actions.failed }));
    if (actions.refused > 0) used.push(t('agentActivity.runSummary.refused', { count: actions.refused }));
    lines.push(used.join(' · '));
  }
  const touched: string[] = [];
  if (records.read > 0) touched.push(t('agentActivity.runSummary.read', { count: records.read }));
  if (records.changed > 0) touched.push(t('agentActivity.runSummary.changed', { count: records.changed }));
  if (touched.length > 0) lines.push(touched.join(' · '));
  if (digest.artifacts.length > 0) {
    const names = digest.artifacts.slice(0, NAMED_FILES).map((a) => a.name);
    const more = digest.artifacts.length - names.length;
    const listed = more > 0 ? [...names, t('agentActivity.summary.more', { count: more })] : names;
    lines.push(t('agentActivity.runSummary.saved', { names: listed.join(', ') }));
  }
  if (approvals.asked > 0) {
    const asked = [t('agentActivity.runSummary.approvals', { count: approvals.asked })];
    if (approvals.approved > 0) asked.push(t('agentActivity.runSummary.approved', { count: approvals.approved }));
    if (approvals.denied > 0) asked.push(t('agentActivity.runSummary.denied', { count: approvals.denied }));
    if (approvals.unanswered > 0) asked.push(t('agentActivity.runSummary.unanswered', { count: approvals.unanswered }));
    lines.push(asked.join(' · '));
  }
  if (digest.cost !== null && (digest.cost.usdMicros > 0 || digest.cost.tokens > 0)) {
    const cost = t('agentActivity.runSummary.cost', { usd: usd(digest.cost.usdMicros), tokens: count(digest.cost.tokens) });
    lines.push(digest.costEstimated === true ? `${cost} · ${t('common.estimatedCharge.label')}` : cost);
  }
  return lines;
}

/**
 * The run's summary at the foot of its steps, once it ended. Read when the person first opens the steps, so a
 * long conversation does not read every run's journal on load.
 */
export function RunSummary({ run }: { run: DurableAgentRunHandle | null }) {
  const { t } = useTranslation();
  const open = useContext(ActivitySectionOpen);
  const [opened, setOpened] = useState(open);
  useEffect(() => {
    if (open) setOpened(true);
  }, [open]);
  const session = run ? { ...(run.agentId ? { agentId: run.agentId } : {}), sessionId: run.sessionId } : null;
  const { timeline } = useRunTimeline(session, run?.runId, { enabled: opened && run !== null });
  const digest = timeline && run && timeline.runId === run.runId ? timeline.digest : undefined;
  if (!digest) return null;
  return (
    <Box data-testid="run-summary" style={{ marginTop: 'var(--space-2)' }}>
      <Text as="div" size="1" weight="medium" style={{ color: 'var(--slate-11)' }}>
        {t('agentActivity.runSummary.title')}
      </Text>
      <Flex direction="column" gap="1" asChild>
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {runSummaryLines(digest, t).map((line) => (
            <li key={line}>
              <Text size="1" style={{ color: 'var(--slate-11)' }}>
                {line}
              </Text>
            </li>
          ))}
        </ul>
      </Flex>
    </Box>
  );
}
